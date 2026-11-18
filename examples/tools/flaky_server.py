"""A small, deliberately unreliable effects service. Stdlib only.

Nothing real is ever charged. The server keeps an in-memory ledger, can fail in
five deterministic ways, and honours ``Idempotency-Key`` the way a payment API
does: the first request is applied and remembered, a repeat of the same key
replays the stored response instead of applying the effect again.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

FAULTS = ("timeout_once", "rate_limit_once", "truncated_body", "stale_read", "schema_drift")
EFFECTS = {"/refund": "refund", "/email": "email", "/records": "record"}
REQUIRED_FIELDS = {
    "/refund": ("customer_id", "amount"),
    "/email": ("to", "subject", "audience"),
    "/records": ("record_id", "value"),
}


class FlakyError(Exception):
    """Base class for failures the client can see."""


class RateLimited(FlakyError):
    """429 from the server."""


class TruncatedResponse(FlakyError):
    """Body ended mid-JSON."""


class SchemaError(FlakyError):
    """Response did not match the documented shape."""


class StaleRead(FlakyError):
    """The server reported the effect as not applied when it was."""


class ServerError(FlakyError):
    """5xx or transport failure."""


RETRYABLE = (TimeoutError, RateLimited, TruncatedResponse, SchemaError, StaleRead)


class Ledger:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(
        self, kind: str, payload: dict[str, Any], idempotency_key: str | None
    ) -> dict[str, Any]:
        with self._lock:
            row = {
                "seq": len(self._rows) + 1,
                "kind": kind,
                "idempotency_key": idempotency_key,
                **payload,
            }
            self._rows.append(row)
            return row

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._rows]

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()

    def money(self) -> float:
        return round(
            sum(float(row["amount"]) for row in self.snapshot() if row["kind"] == "refund"),
            2,
        )


class Faults:
    """Per-endpoint fault queues, consumed one request at a time."""

    def __init__(self, plan: dict[str, list[str]] | None = None) -> None:
        self._plan: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self.arm(plan or {})

    def arm(self, plan: dict[str, list[str]]) -> None:
        with self._lock:
            self._plan = {endpoint: list(faults) for endpoint, faults in plan.items()}
            for faults in self._plan.values():
                unknown = set(faults) - set(FAULTS)
                if unknown:
                    raise ValueError(f"unknown faults: {sorted(unknown)}")

    def take(self, endpoint: str) -> str | None:
        with self._lock:
            queue = self._plan.get(endpoint) or []
            return queue.pop(0) if queue else None


class _State:
    def __init__(self, faults: dict[str, list[str]] | None, seed: int) -> None:
        self.ledger = Ledger()
        self.faults = Faults(faults)
        self.seed = seed
        self.idempotent: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # noqa: D102 - silence the test server
        return

    @property
    def state(self) -> _State:
        return self.server.state  # type: ignore[attr-defined]

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if urlparse(self.path).path == "/ledger":
            self._send_json(
                200,
                {
                    "rows": self.state.ledger.snapshot(),
                    "money_moved": self.state.ledger.money(),
                    "seed": self.state.seed,
                },
            )
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid json"})
            return

        if path == "/reset":
            self.state.ledger.clear()
            with self.state.lock:
                self.state.idempotent.clear()
            self.state.faults.arm(payload.get("faults") or {})
            self._send_json(200, {"ok": True, "reset": True})
            return

        if path not in EFFECTS:
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        missing = [name for name in REQUIRED_FIELDS[path] if name not in payload]
        if missing:
            self._send_json(400, {"ok": False, "error": f"missing {missing}"})
            return

        key = self.headers.get("Idempotency-Key")
        if key:
            with self.state.lock:
                stored = self.state.idempotent.get(key)
            if stored is not None:
                replay = dict(stored)
                replay["deduplicated"] = True
                self._send_json(200, replay)
                return

        fault = self.state.faults.take(path)
        if fault == "rate_limit_once":
            self._send_json(429, {"ok": False, "error": "rate limited"})
            return

        row = self.state.ledger.add(EFFECTS[path], payload, key)
        response = {"ok": True, "ledger_id": row["seq"], "kind": row["kind"]}
        if key:
            with self.state.lock:
                self.state.idempotent[key] = response

        if fault == "timeout_once":
            # Applied, then the connection goes quiet: the classic duplicate-write setup.
            time.sleep(0.8)
            self.close_connection = True
            return
        if fault == "truncated_body":
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body) + 40))
            self.end_headers()
            self.wfile.write(body[:5])
            self.close_connection = True
            return
        if fault == "schema_drift":
            self._send_json(200, {"ok": True, "ref": f"txn_{row['seq']}"})
            return
        if fault == "stale_read":
            self._send_json(200, {"ok": True, "applied": False, "ledger_id": row["seq"]})
            return
        self._send_json(200, response)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: _State) -> None:
        super().__init__(address, _Handler)
        self.state = state


class FlakyServer:
    """Start on an ephemeral port; use as a context manager."""

    def __init__(
        self,
        faults: dict[str, list[str]] | None = None,
        seed: int = 7,
        host: str = "127.0.0.1",
    ) -> None:
        self.state = _State(faults, seed)
        self._httpd = _Server((host, 0), self.state)
        self.host, self.port = self._httpd.server_address[0], self._httpd.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> FlakyServer:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def reset(self, faults: dict[str, list[str]] | None = None) -> None:
        self.state.ledger.clear()
        with self.state.lock:
            self.state.idempotent.clear()
        self.state.faults.arm(faults or {})

    def ledger(self) -> list[dict[str, Any]]:
        return self.state.ledger.snapshot()

    def money(self) -> float:
        return self.state.ledger.money()

    def __enter__(self) -> FlakyServer:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


class FlakyClient:
    """Client that surfaces each fault as a typed, retryable exception."""

    def __init__(self, base_url: str, timeout: float = 0.35) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def refund(
        self, customer_id: str, amount: float, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._post(
            "/refund",
            {"customer_id": customer_id, "amount": amount},
            idempotency_key,
        )

    def email(
        self,
        to: str,
        subject: str,
        audience: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._post(
            "/email",
            {"to": to, "subject": subject, "audience": audience},
            idempotency_key,
        )

    def record(
        self, record_id: str, value: Any, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        return self._post(
            "/records", {"record_id": record_id, "value": value}, idempotency_key
        )

    def ledger(self) -> dict[str, Any]:
        return self._get("/ledger")

    def reset(self, faults: dict[str, list[str]] | None = None) -> dict[str, Any]:
        return self._post("/reset", {"faults": faults or {}}, None)

    def _get(self, path: str) -> dict[str, Any]:
        try:
            with urlopen(self.base_url + path, timeout=self.timeout) as response:
                return json.loads(response.read())
        except TimeoutError as exc:
            raise TimeoutError(f"client timed out after {self.timeout:g}s") from exc
        except URLError as exc:
            raise ServerError(str(exc.reason)) from exc

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if idempotency_key:
            request.add_header("Idempotency-Key", idempotency_key)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except HTTPError as exc:
            if exc.code == 429:
                raise RateLimited("429 from server") from exc
            raise ServerError(f"{exc.code} from server") from exc
        except TimeoutError as exc:
            raise TimeoutError(f"client timed out after {self.timeout:g}s") from exc
        except (http.client.IncompleteRead, ConnectionError) as exc:
            raise TruncatedResponse("connection closed mid-body") from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError(f"client timed out after {self.timeout:g}s") from exc
            raise ServerError(str(exc.reason)) from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise TruncatedResponse("response body ended mid-JSON") from exc
        if not isinstance(parsed, dict) or "ok" not in parsed:
            raise SchemaError(f"unexpected response: {parsed!r}")
        if parsed.get("applied") is False:
            raise StaleRead("server says the effect was not applied")
        if "ledger_id" not in parsed:
            raise SchemaError(f"response missing ledger_id: {parsed!r}")
        return parsed
