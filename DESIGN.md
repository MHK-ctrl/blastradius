# DESIGN — `blastradius`

**One page. What the guard promises, and what it does not.**

## What counts as an effect

An **effect** is any call that crosses the guard decorator into a function that can change state
outside the process: a charge, a refund, an email, a record write. Pure computation is not an
effect and should not be wrapped. The unit of protection is the **intent**:
`(session_id, effect_name, sha256(canonical payload))`. Two calls with the same session, effect and
semantically identical arguments are the same intent, even if they arrive minutes apart, on
different threads, or after a crash.

## How the key is derived, and why intent beats attempt

```
payload  -> normalise (48.0 -> 48, key order irrelevant, Decimal/date -> JSON scalar)
         -> canonical JSON (sorted keys, no whitespace)
         -> sha256(effect_name + "\x00" + canonical_json)
         -> idempotency key = "br_" + sha256(session_id + "\x00" + effect_name + "\x00" + hash)
```

An attempt-derived key (`uuid4()` per call) makes every retry a new request, which is the bug, not
the fix. An intent-derived key is stable across retries, so a callee that honours idempotency keys
replays the first result instead of performing the effect twice, and the journal can recognise a
duplicate without any network round trip. Collision risk is a second-preimage problem on SHA-256;
for a payload space this small it is not a practical concern. The real risk is **semantic**
collision: two genuinely different user requests that serialise identically (same customer, same
amount, same reason) are deliberately collapsed. That is the intended behaviour for a retry and the
wrong behaviour for a repeated purchase; `session_id` is the boundary that separates them.

## Crash between `pending` and `done`

The row is written `pending` **before** the call. If the process dies after the call but before the
`done` update, the journal cannot know whether the effect landed. `Journal.recover()` therefore
never decides on its own: with no `assume` argument it returns the stuck rows and changes nothing;
`recover(assume="failed")` and `recover(assume="done")` are explicit caller decisions, both
recorded in the row's `error` field. A guard that finds a matching `pending` row refuses to
re-execute and raises `PendingEffect`, because re-running is a guess with a real cost.

## What the guard cannot guarantee

- **Effects written outside the tool boundary.** Anything that bypasses the decorator — a direct
  SDK call, a background worker, a human in the dashboard — is invisible.
- **A callee that ignores the key.** The guard forwards an `idempotency_key` and records what the
  callee declares (`deduplicated: true`). It cannot force a third party to honour it. In-process
  collapse of a *completed* intent is guaranteed; cross-process exactly-once is a property of the
  callee, not of this library.
- **Clock skew and ordering.** Timestamps are local wall clock; reverse-order rollback uses rowid
  order, not wall-clock order, but undo side effects at the callee may still interleave.
- **Retries you never see.** A queue or proxy retrying underneath your process is outside the
  journal.
- **Undo that is not really an undo.** The library guarantees the handler is called, once, in
  reverse order, and that a failure is loud. It cannot guarantee your handler restores the world.

## Failure semantics

| Situation | Journal state | Caller sees |
|---|---|---|
| Success | `done` | return value |
| Duplicate of a `done` intent | second row, `collapsed=1` | the first result |
| Exception | `failed` | the original exception |
| Out of scope | `blocked` | `ScopeViolation`, function never runs |
| Crash after call, before update | `pending` | `PendingEffect` until resolved |
| Rollback | `unrolled` / `unrolled_failed` | `UndoFailure` listing every failed undo |

A `failed` intent may be retried; `attempt` is incremented and the key is unchanged. A retry after a
timeout is exactly the case where the callee's idempotency support decides the outcome, and the
report says so explicitly (`retries_after_failure` vs `callee_deduped` vs `duplicate_writes`).
