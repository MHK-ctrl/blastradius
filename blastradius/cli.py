"""`br` command line: report, verify, dryrun, index."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence

from .report import build_report, format_report
from .scope import default_db_path
from .storage.sqlite_store import SqliteStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="br",
        description="Exactly-once, undoable side effects for AI agents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="summarise a journal")
    report.add_argument("--db", default=None, help="path to the journal database")
    report.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    report.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if duplicate_writes > 0 (for CI)",
    )

    verify = sub.add_parser("verify", help="list effects stuck in 'pending'")
    verify.add_argument("--db", default=None, help="path to the journal database")
    verify.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    dry = sub.add_parser("dryrun", help="run a command against the shadow ledger")
    dry.add_argument("--db", default=None, help="path to the journal database")
    dry.add_argument("command", nargs=argparse.REMAINDER, help="command after --")

    index = sub.add_parser("index", help="run the Blast Radius Index harness")
    index.add_argument("--trials", type=int, default=5, help="trials per scenario (default 5)")
    index.add_argument(
        "--scenarios",
        default=None,
        help="directory of scenario YAML files (default: index/scenarios)",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "report":
        return _report(args)
    if args.command == "verify":
        return _verify(args)
    if args.command == "dryrun":
        return _dryrun(args)
    if args.command == "index":
        return _index(args)
    return 2


def _report(args: argparse.Namespace) -> int:
    store = SqliteStore(args.db or default_db_path())
    try:
        data = build_report(store)
    finally:
        store.close()
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(format_report(data))
    if args.strict and data["duplicate_writes"] > 0:
        print(
            f"strict: {data['duplicate_writes']} duplicate write(s) found",
            file=sys.stderr,
        )
        return 1
    return 0


def _verify(args: argparse.Namespace) -> int:
    store = SqliteStore(args.db or default_db_path())
    try:
        rows = store.rows(state="pending")
    finally:
        store.close()
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": row.id,
                        "session_id": row.session_id,
                        "effect_name": row.effect_name,
                        "attempt": row.attempt,
                        "created_at": row.created_at,
                        "payload": row.payload_obj,
                    }
                    for row in rows
                ],
                indent=2,
                sort_keys=True,
            )
        )
    elif not rows:
        print("verify: no pending effects")
    else:
        print(f"verify: {len(rows)} pending effect(s) — outcome unknown, resolve explicitly")
        for row in rows:
            print(
                f"  {row.id}  {row.effect_name:<12} session={row.session_id}  "
                f"attempt={row.attempt}  created={row.created_at}"
            )
    return 1 if rows else 0


def _dryrun(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("dryrun: nothing to run; usage: br dryrun -- <command>", file=sys.stderr)
        return 2
    db_path = args.db or default_db_path()
    env = dict(os.environ)
    env["BLASTRADIUS_DB"] = db_path
    env["BLASTRADIUS_DRY_RUN"] = "1"
    completed = subprocess.run(command, env=env, check=False)
    store = SqliteStore(db_path)
    try:
        from .dryrun import diff

        shadow = diff(store)
    finally:
        store.close()
    print("\nshadow ledger (nothing was executed)")
    print(json.dumps(shadow, indent=2, sort_keys=True))
    return completed.returncode


def _index(args: argparse.Namespace) -> int:
    command = [sys.executable, "-m", "index.runner", "--trials", str(args.trials)]
    if args.scenarios:
        command += ["--scenarios", args.scenarios]
    try:
        return subprocess.run(command, cwd=os.getcwd(), check=False).returncode
    except FileNotFoundError as exc:  # pragma: no cover - environment specific
        print(f"index: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
