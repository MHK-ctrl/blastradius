# LinkedIn launch post — draft, NOT posted

Rules this draft follows: under 2,900 characters, first person, short lines, no
hype, every number from my own measured run, simulated data labelled in the same
sentence as the number, one unedited trace, the cost stated, one limitation
stated, no endorsement claims, no vendor disparagement.

## Three opening-line options (each under 140 characters)

1. An agent retried a refund after a timeout. The customer was charged twice.  (77)
2. One timeout. Two refunds. $80. The fix is not a better prompt.  (65)
3. Everyone measures what agents can do. Almost nobody measures what happens when they do it twice.  (99)

## Body

<!-- BODY START -->
An agent retried a refund after a timeout. The customer was charged twice.

The server had applied the first refund. Only the response went missing.
So the agent did what agents do: it tried again.

One raw trial from the index, guard off (money-01, seed 2; simulated run):
{"seq": 1, "kind": "refund", "idempotency_key": null, "customer_id": "cust_17", "amount": 40}
{"seq": 2, "kind": "refund", "idempotency_key": null, "customer_id": "cust_17", "amount": 40}
attempt 1 had raised: TimeoutError: client timed out after 0.35s.

So I built blastradius, a guard between the agent and its tools:
- intent-derived idempotency keys, so a retry is a no-op
- a write-ahead journal, so a crash is recoverable, not a mystery
- an undo handler per effect, so a whole run can be rolled back
- scope tokens, so "refund <= $50" is enforced in code, not in a prompt

Same task, same seed, same fault, guard on: one refund. duplicate_writes: 0.

Then I built the measurement harness: 45 scenarios, 5 trials, 2 arms, 450 tasks, the same seeds and faults in both arms. The oracle is the tool server's ledger, not the guard's journal.

Simulated result, because no model API key was present and both arms are scripted policies, not LLMs: duplicate-or-unauthorised writes per 1,000 tasks were 800.0 with the guard off (n=225, 95% CI 742.9-847.0) and 0.0 with it on (n=225, 95% CI 0.0-16.8).

Cost: +0.010 ms p50 per effect, about 3.7% against a local tool round trip. p95 landed inside noise. 1,256 lines of Python.

What it does not fix: the guard forwards an idempotency key; it cannot make the other side honour it. A retry below your process never reaches this journal.

Code and the index: https://github.com/MHK-ctrl/blastradius
Live leaderboard: https://mhk-ctrl.github.io/blastradius/

Built this because everyone is measuring what agents can do, and almost nobody is measuring what happens when they do it twice.

The index has no row yet for LangGraph (@LangChain), the OpenAI Agents SDK (@OpenAI), or the Anthropic tool-use loop (@Anthropic). I had no key, so I published no number.

#AIAgents #Reliability
<!-- BODY END -->

## First comment (paste after posting)

An open question for teams running agent evals: the scenario format is four fields
and a YAML file, and the ledger is the only oracle. If your stack is in the list
below and you want a row, the harness and the rules are in the repo — the method
page says exactly what a row requires.

@Scale AI @Databricks @IBM @Salesforce @Stripe @Uber

## Tagging plan (deliberate, not carpet-bombing)

- **Body: 3 tags.** @LangChain, @OpenAI and @Anthropic are each named in the last
  paragraph, because those are the unmeasured agent stacks the index would cover
  next. Nothing is claimed about their behaviour and none of them endorsed this.
- **First comment: 6 tags, framed as an open question**, listed above. They are not
  in the body because the post does not discuss their published work.
- **Mechanics:** on desktop, type `@`, then the page name, then **select it from the
  dropdown**. A typed name with no dropdown pick is not a tag. If a page does not
  appear in the mention list, use the plain name and note it in the comment.
- Do not tag a company that is not named in the text. Do not repeat this cluster on
  the next post.

## Manual checklist for me

- `gh auth login` — already done for this run (`repo`, `workflow` scopes).
- Add Actions secrets **by name only** to enable a live (non-simulated) index run:
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (optional). Never paste a key into a chat.
- Paste the body above, pick the three tags from the dropdown, add the first
  comment, attach the before/after screenshot (RUN A / RUN B from the demo).
- Post Tuesday–Thursday, morning my time. Reply to every comment for the first two
  hours.
