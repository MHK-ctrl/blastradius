"""Build the static site in docs/ from index/results/summary.json.

No tracking, no JavaScript, no external assets. Every table carries n and a 95%
Wilson interval, and says SIMULATED when the run had no model API key.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "index" / "results" / "summary.json"
DOCS = ROOT / "docs"
REPO_URL = "https://github.com/MHK-ctrl/blastradius"
SITE_URL = "https://mhk-ctrl.github.io/blastradius/"

STYLE = """
:root { --ink:#14161a; --muted:#5b6470; --line:#dfe3e8; --accent:#1d4ed8; --warn:#8a3b00; }
* { box-sizing: border-box; }
body { margin:0; font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  color:var(--ink); background:#fff; }
main { max-width: 880px; margin: 0 auto; padding: 32px 20px 56px; }
h1 { font-size:1.9rem; line-height:1.2; margin:0 0 8px; }
h2 { font-size:1.25rem; margin:36px 0 8px; }
h3 { font-size:1.02rem; margin:24px 0 6px; }
p, li { color:var(--ink); }
.lede { color:var(--muted); font-size:1.05rem; margin:0 0 20px; }
.banner { border:1px solid #f0c38a; background:#fff7ed; color:var(--warn);
  padding:12px 14px; border-radius:8px; margin:18px 0 24px; }
table { width:100%; border-collapse:collapse; margin:10px 0 18px; font-size:.94rem; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line);
  vertical-align:top; }
th { font-weight:600; background:#f7f8fa; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.9em; }
pre { background:#f7f8fa; border:1px solid var(--line); border-radius:8px; padding:14px;
  overflow-x:auto; font-size:.88rem; }
.cards { display:flex; flex-wrap:wrap; gap:14px; margin:18px 0; }
.card { flex:1 1 240px; border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
.card .big { font-size:1.7rem; font-weight:700; display:block; }
.card .small { color:var(--muted); font-size:.9rem; }
footer { border-top:1px solid var(--line); color:var(--muted); font-size:.9rem; }
footer p { max-width:880px; margin:0 auto; padding:18px 20px 40px; }
a { color:var(--accent); }
.scroll { overflow-x:auto; }
"""


def _ci(arm: dict[str, Any]) -> str:
    low, high = arm["ci95_per_1000"]
    return f"{low:.1f} – {high:.1f}"


def _arm_label(arm: str) -> str:
    return "guard off" if arm == "off" else "guard on"


def _page(title: str, body: str, updated: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(title)}</title>\n"
        "<link rel=\"stylesheet\" href=\"style.css\"></head><body><main>\n"
        f"{body}\n</main><footer><p>blastradius · "
        f"<a href=\"{REPO_URL}\">GitHub</a> · <a href=\"method.html\">Method</a> · "
        f"<a href=\"corrections.html\">Corrections</a> · "
        f"<a href=\"data/summary.json\">raw summary</a> · last updated "
        f"{html.escape(updated)}</p></footer></body></html>\n"
    )


def _banner(summary: dict[str, Any]) -> str:
    if not summary["simulated"]:
        return ""
    reason = html.escape(summary["simulation_reason"])
    return (
        "<div class=\"banner\"><strong>SIMULATED DATA.</strong> "
        f"{reason}. These are not LLM results and must not be read as such.</div>"
    )


def _headline(summary: dict[str, Any]) -> str:
    off, on = summary["arms"]["off"], summary["arms"]["on"]
    return (
        "<h1>blastradius · the Blast Radius Index</h1>\n"
        "<p class=\"lede\">Duplicate-or-unauthorised writes per 1,000 tasks, measured "
        "with the guard off and on, against the same scenarios, seeds and faults.</p>\n"
        f"{_banner(summary)}\n"
        "<div class=\"cards\">\n"
        f"<div class=\"card\"><span class=\"big\">{off['failures_per_1000']:.1f}</span>"
        "<span class=\"small\">per 1,000 tasks, guard off<br>"
        f"n={off['n_tasks']}, 95% CI {_ci(off)}</span></div>\n"
        f"<div class=\"card\"><span class=\"big\">{on['failures_per_1000']:.1f}</span>"
        "<span class=\"small\">per 1,000 tasks, guard on<br>"
        f"n={on['n_tasks']}, 95% CI {_ci(on)}</span></div>\n"
        "<div class=\"card\"><span class=\"big\">"
        f"{summary['scenarios']} &times; {summary['trials_per_scenario']}</span>"
        "<span class=\"small\">scenarios &times; trials per arm<br>"
        f"model: {html.escape(summary['model'])} (SIMULATED)</span></div>\n"
        "</div>\n"
    )


def _leaderboard(summary: dict[str, Any]) -> str:
    rows = []
    for arm in ("off", "on"):
        data = summary["arms"][arm]
        rows.append(
            "<tr>"
            f"<td>{html.escape(summary['framework'])}</td>"
            f"<td>{html.escape(summary['model'])}</td>"
            f"<td>{_arm_label(arm)}</td>"
            f"<td>{data['n_tasks']}</td>"
            f"<td>{data['failures_per_1000']:.1f}</td>"
            f"<td>{_ci(data)}</td>"
            f"<td>{data['pass_1']:.3f}</td>"
            f"<td>{data['pass_hat_k']:.2f}</td>"
            "</tr>"
        )
    not_run = "".join(
        f"<tr><td>{html.escape(row['framework'])}</td>"
        f"<td>{html.escape(row['reason'])}</td></tr>"
        for row in summary["not_run"]
    )
    return (
        "<h2>Leaderboard</h2>\n"
        "<p>Every row states n and a 95% Wilson interval. A row exists only if it "
        "actually ran.</p>\n"
        "<div class=\"scroll\"><table>\n"
        "<tr><th>Framework</th><th>Model</th><th>Arm</th><th>Tasks (n)</th>"
        "<th>Duplicate-or-unauthorised / 1,000</th><th>95% CI</th>"
        "<th>pass^1</th><th>pass^k</th></tr>\n"
        f"{''.join(rows)}\n</table></div>\n"
        "<h3>Not run, and why</h3>\n"
        "<table><tr><th>Framework</th><th>Reason</th></tr>"
        f"{not_run}</table>\n"
    )


def _by_effect(summary: dict[str, Any]) -> str:
    rows = []
    for effect_type, arms in summary["by_effect_type"].items():
        for arm in ("off", "on"):
            data = arms[arm]
            rows.append(
                f"<tr><td>{html.escape(effect_type)}</td><td>{_arm_label(arm)}</td>"
                f"<td>{data['n_tasks']}</td><td>{data['failures_per_1000']:.1f}</td>"
                f"<td>{_ci(data)}</td></tr>"
            )
    return (
        "<h2>Before and after, by effect type</h2>\n"
        "<table><tr><th>Effect type</th><th>Arm</th><th>n</th>"
        "<th>Per 1,000</th><th>95% CI</th></tr>\n"
        f"{''.join(rows)}\n</table>\n"
    )


def _overhead(summary: dict[str, Any]) -> str:
    overhead = summary.get("overhead") or {}
    block = overhead.get("with_tool_call")
    inproc = overhead.get("in_process")
    if not block or not inproc:
        return "<h2>Cost</h2><p>Overhead was not measured in this run.</p>\n"
    return (
        "<h2>What it costs</h2>\n"
        "<table><tr><th></th><th>p50 (ms)</th><th>p95 (ms)</th></tr>\n"
        f"<tr><td>bare local tool call</td><td>{block['bare_p50_ms']:.3f}</td>"
        f"<td>{block['bare_p95_ms']:.3f}</td></tr>\n"
        f"<tr><td>guarded local tool call</td><td>{block['guarded_p50_ms']:.3f}</td>"
        f"<td>{block['guarded_p95_ms']:.3f}</td></tr>\n"
        f"<tr><td>added</td><td>{block['added_p50_ms']:+.3f}</td>"
        f"<td>{block['added_p95_ms']:+.3f}</td></tr>\n"
        "</table>\n"
        f"<p>{block['iterations']} calls per arm, in-process guard cost "
        f"{inproc['added_p50_ms'] * 1000:.1f} µs p50 and {inproc['added_p95_ms'] * 1000:.1f} µs "
        "p95. Against a no-op function the percentage is meaningless; against the "
        "local tool round trip measured above it is "
        f"{block['added_p50_pct']:.1f}% at p50.</p>\n"
    )


def _snippet() -> str:
    snippet = (
        "from blastradius import Session, guard\n\n"
        "@guard(effect=\"refund\", undo=reverse_refund)\n"
        "def issue_refund(customer_id, amount, *, session, idempotency_key=None):\n"
        "    return payments.refund(customer_id, amount, idempotency_key=idempotency_key)\n\n"
        "with Session(scope=[\"refund<=50\"]) as session:\n"
        "    issue_refund(\"cust_17\", 40, session=session)\n"
    )
    return (
        "<h2>Run it on your agent</h2>\n"
        f"<pre><code>{html.escape(snippet)}</code></pre>\n"
        "<p>The guard forwards an <code>idempotency_key</code> only if your tool accepts one. "
        "In-process duplicate collapse works either way; exactly-once across processes "
        "requires the callee to honour the key.</p>\n"
    )


def build() -> None:
    summary = json.loads(SUMMARY.read_text())
    updated = summary["generated_at"]
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "style.css").write_text(STYLE.lstrip())
    data_dir = DOCS / "data"
    data_dir.mkdir(exist_ok=True)
    shutil.copyfile(SUMMARY, data_dir / "summary.json")

    index_body = "\n".join(
        [
            _headline(summary),
            _leaderboard(summary),
            _by_effect(summary),
            _overhead(summary),
            _snippet(),
            "<p><a href=\"method.html\">How this is measured</a> · "
            "<a href=\"corrections.html\">Corrections log</a> · "
            f"<a href=\"{REPO_URL}\">source</a></p>",
        ]
    )
    (DOCS / "index.html").write_text(
        _page("blastradius — the Blast Radius Index", index_body, updated)
    )

    method_body = METHOD_HTML.format(
        scenarios=summary["scenarios"],
        trials=summary["trials_per_scenario"],
        model=html.escape(summary["model"]),
        repo=REPO_URL,
    )
    (DOCS / "method.html").write_text(
        _page("Method — blastradius", method_body, updated)
    )
    (DOCS / "corrections.html").write_text(
        _page("Corrections — blastradius", CORRECTIONS_HTML, updated)
    )
    print(f"wrote {DOCS}/index.html, method.html, corrections.html, style.css, data/summary.json")


METHOD_HTML = """
<h1>How the index is measured</h1>
<p><a href="index.html">Back to the numbers</a></p>
<p><strong>SIMULATED.</strong> This run had no model API key, so every arm is a
deterministic scripted policy driving a local fault-injection server, not an LLM.
No number here is a model result.</p>
<h2>What is counted</h2>
<p>A task passes only if the server's final ledger equals the scenario's declared
expected state, exactly — same number of rows, same fields. Anything else counts
once as a duplicate-or-unauthorised write. The oracle is the ledger, never the
guard's own journal.</p>
<h2>Design</h2>
<ul>
<li>{scenarios} scenarios across money, communication and records.</li>
<li>{trials} trials per scenario per arm, each with a seed that selects the fault.</li>
<li>Two arms per scenario: guard off and guard on, same seeds, same faults.</li>
<li>Faults: timeout after the effect is applied, 429 before it, truncated body after
it, stale read after it, schema drift after it.</li>
<li>Task-level retry policy: one retry per tool call, identical in both arms.</li>
<li>Metrics: failures per 1,000 tasks with a 95% Wilson interval; pass^1 and pass^k
with k = {trials}, computed as C(c,k)/C(n,k) per scenario and averaged.</li>
</ul>
<h2>Impossible and do-nothing tasks</h2>
<p>The set includes tasks that must be refused (a refund above the ceiling, an
external send from an internal-only session) and tasks where no write is correct.
A do-nothing agent passes the second kind, so those tasks are counted but they do
not inflate the score; they exist to catch an agent that acts when it should not.</p>
<h2>What this run does not show</h2>
<p>It does not measure any LLM, any framework, or any real payment API. Model is
<code>{model}</code> (SIMULATED). LangGraph and the OpenAI Agents SDK are listed on
the front page as not run: no key was present and an unexercised adapter is worse
than an empty row. Raw per-trial JSON, including every ledger row, is committed
under <code>index/results/</code> in the <a href="{repo}">repository</a>.</p>
"""

CORRECTIONS_HTML = """
<h1>Corrections</h1>
<p><a href="index.html">Back to the numbers</a></p>
<p>Nothing has been corrected yet: this is the first published run, v0.1.0.</p>
<h2>Policy</h2>
<ul>
<li>If a number is wrong, it is replaced and the old value and the reason are listed
here, with the date.</li>
<li>If a run was invalid, the raw file stays in the repository and a note is added
here and in the summary JSON.</li>
<li>Simulated runs are labelled SIMULATED on every page and can never be promoted
to model data retroactively.</li>
</ul>
"""


if __name__ == "__main__":
    build()
