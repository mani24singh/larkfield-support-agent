# Approach

## Problem Summary

The service reads customer support email requests and returns a structured
decision for each one: the ticket type, the resolved order and customer, an
outcome, the checks and evidence behind it, whether a human must approve,
a draft reply, and a trace. It covers refunds, returns, delivery issues and
order-status questions for the supplied furniture orders.

The constraints shaped the design more than the features did:

- **Proposal only.** The service must never issue a refund, send an email or
  modify an order. Every financial action is a ledger proposal that waits for a
  human decision where the policy requires one.
- **The supplied data is the only truth.** Customer, order, item, shipment and
  prior-refund records come from the read-only CSVs. Order ids and every money
  figure are derived from those records, not from the email and not from a
  model.
- **One policy governs.** RP-3.2 version 3.2 is authoritative. The Help Centre
  FAQ is superseded and the warehouse document is out of scope. No rule is
  invented when the policy is silent.
- **The response schema is fixed.** `schema/response_schema.py` defines the
  request and response bodies; a successful response is schema-valid first and
  useful second.
- **Deployment is a public HTTP service with no authentication** on the port
  supplied by the platform, and no secrets in the code or image.

"Correct" therefore means, for every ticket: the response validates against the
schema; identity and the delivery window are resolved from the data pack; the
outcome follows RP-3.2 in the fixed gate order; money is exact, capped at the
affected unpaid line value, and never paid twice; approval is required exactly
where the policy says so; and the same input produces the same decision on every
run. On the supplied evidence that means 40/40 schema-valid responses, 15/15
agreement with the senior-agent labels, and an idempotent replay.

## Technical Approach

**Architecture.** The code is split so that policy has no I/O and the HTTP layer
has no policy:

- `src/solution.py` is the entry point. `src.api` builds the application through
  `create_app()`; `src.solution` re-exports that single FastAPI instance as
  `app`, which is the uvicorn target, and `main()` serves it on `$PORT`.
- `src/api.py` validates requests, owns the run registry, and exposes the
  endpoints. It contains no decision logic.
- `src/pipeline.py` orchestrates: it resolves identity for the response, calls
  the adjudicator, applies refund idempotency, drafts the reply, and writes the
  response and ledger rows.
- `src/policy.py` is pure. It detects override attempts, resolves identity,
  classifies tickets, evaluates the carrier record and delivery window, selects
  affected lines, computes money, applies authority and emits the checks. It
  receives records and an explicit `received_at`/`now`; it never reads files,
  reads the clock or calls a model.
- `src/ingest.py` loads the five CSVs into immutable indexes and normalises
  emails, order references, dates and timestamps. `src/money.py` parses and
  quantises money.
- `src/ledger.py` owns run state, append-only JSONL entries, idempotency keys
  and approvals, with atomic file replacement under `runs/<run_id>/`.
- `src/llm.py` is a provider-agnostic OpenAI-compatible client. `src/prompts.py`
  holds the extraction and reply prompts, plus the per-outcome fallback
  templates.
- `src/models.py` re-exports the supplied schema so there is one model source.

**Data flow.** The CSV pack is loaded once and cached: 47 unique orders (49 rows
minus two identical duplicates), 53 customers, 57 order lines, 47 shipments and
3 prior refunds. A request flows through normalisation, identity resolution,
carrier evaluation, delivery-window evaluation, affected-line selection, prior
refund deduction, money computation, authority and idempotency. The same path
also assembles checks with `source`/`ref`/`value` evidence and an RP-3.2 clause
for each check. Responses and ledger rows are written before the HTTP response
is returned.

**Decision order.** Every ticket runs the same ordered gates and the first
blocking gate decides:

1. Safety — override attempts, threats, claimed pre-approval.
2. Identity — exact order, email match, owner check.
3. Carrier — status available, lost in transit, moving.
4. Window — 30 days from carrier delivery, or 45 for damage with a photo.
5. Scope — affected lines and quantities only.
6. Prior refunds — subtract refunds already issued.
7. Refunds — affected quantity times unit price, capped.
8. Modification — reduce over-reaching requests instead of honouring them.
9. Authority — above $150.00 pends for a Support Lead; exactly $150.00 proceeds.
10. Idempotency — one refund per customer, order and line per run.

**Why the money logic is deterministic.** Money never passes through the model.
Every amount is computed from the data pack in decimal arithmetic: the affected
quantity is capped at the ordered quantity, the gross value is quantity times
unit price, prior `ISSUED` refunds are deducted per line, shipping is excluded,
and the result cannot exceed the unpaid affected-line value. Thresholds are
compared exactly, so $150.00 proceeds and $150.01 pends. The same inputs produce
the same Decimal output; tests cover the boundary values and the caps.

**What the model is allowed to do.** The extraction prompt returns advisory facts
(order reference as written, product names, damage/wrong-item/non-delivery
flags, claimed amounts, override quote). Those facts never set the canonical
order id or any money. The reply prompt receives an already-decided outcome and
can only write prose. Its output is post-validated: amount present if and only if
the outcome is `PROCEED` or `PROCEED_MODIFIED`, every dollar figure equal to the
decision amount, 90–160 words, first name present, fixed sign-off, no internal
codes and no mention of AI or policy engines. Failures fall back to a template
without changing the outcome. The email itself is always treated as untrusted
data, both in the prompts and in the deterministic override detection.

**Idempotency, ledger and approvals.** A refund key is the normalised sender
email, the canonical order id and the affected line. If a key already has a
`PROPOSED` or `APPROVED` refund in the run, the duplicate gets a reply saying the
request is already being handled and no second financial row is created. The
ledger is append-only JSONL: `REFUND` rows carry an amount and start as
`PROPOSED`; `ESCALATION` rows carry none; status answers create `REPLY` rows; and
`REJECT`, `INVALID_REQUEST` and `UNABLE_TO_VERIFY` create no financial row. Only
`PEND` and `ESCALATE` tickets can be approved; a repeat approval is a 200 no-op,
the opposite decision is a 409. Responses and ledger are replaced atomically
under `runs/<run_id>/`.

**Evaluation.** `eval/run_eval.py` processes all 40 public tickets, validates
every response against the schema, compares the 15 labelled tickets, runs the
whole set twice for determinism, and replays the batch to confirm that the
refund count does not grow. It writes `reports/eval_report.md` with metrics, a
failure list and a per-ticket table, and exits non-zero if any check fails.

## AI Tool Usage

Development was done with OpenCode against the Cadra provider, configured through
environment variables in `opencode.json` (base URL and token were never written
into the repository). The work was split into reviewable packages, roughly one
per commit: scaffold, ingest and money, policy gates, money and authority,
adjudicator plus labelled eval, HTTP and ledger, model client and reply step,
adversarial tests and offline evaluation, Docker and deployment, the artefact,
and the Render entry-point fix.

The prompts that mattered most were the ones that pinned down acceptance rather
than description:

- Quoting RP-3.2 clauses and the schema fields directly, instead of asking for
  "a refund policy".
- Naming the exact test cases: 150.00 versus 150.01, one of two chairs, already
  refunded, 40 days with and without a photo, 10 days versus 10 days and hours,
  T002/T027 duplicates, double approval and a 409 on a rejected ticket.
- Hard constraints stated up front: no hardcoded ticket ids, money as `Decimal`,
  timezone-aware dates, reason codes only from the policy vocabulary, and no
  invented rules.
- "Never change the test expectations" and "never special-case a ticket id" when
  the labelled seed was introduced.

The agent got several things wrong, and the correction usually came from a test
or from reading the data rather than from a re-prompt:

- The first runbook pointed uvicorn at `src.app:app` while the entry point was
  `src.solution`. The deployment instructions and tests were corrected.
- Ingest initially missed an `import re`, and its `data_dir` override was
  ignored because the config paths took precedence. Both were caught by the
  loader tests.
- Carrier movement used `max(last_event_at, shipped_at)`, which hid a stale
  tracking feed and kept T011 as "moving". The seed test forced the rule to
  trust `last_event_at`; later the comparison against ten days used whole days,
  so "10 days and two hours" still looked moving. It now compares the full
  timedelta, with exactly ten days still moving.
- Override detection missed T021's wording ("as the support agent you are
  authorised… above any limit… `[system]`"). Patterns were added for role
  claims, limit bypass and system tags.
- The labelled-eval test was first placed in `tests/test/`, which plain pytest
  does not collect. It was moved to `tests/test_eval_seed.py`.
- A template rewording dropped the "duplicate refund" phrase that the duplicate
  test asserts. The template was fixed and now validates like model output.
- The first deployment failure was not a code defect on the branch: `origin/main`
  was ten commits behind, so Render was building the original stub whose
  `solution.py` only had `main()`. The export was made explicit (one canonical
  `app` re-exported rather than a second instance) and `main` was pushed.

## Trade-offs & Limitations

- **Rule-based classification and extraction.** Product, quantity, damage and
  override detection use regular expressions and keyword sets. They handle the
  supplied phrasings and the adversarial seed, but new wording can slip through
  or be over-matched. The LLM extraction step is advisory and is not on the
  decision path.
- **The model was not exercised live during evaluation.** No Cadra key was
  configured, so the 40-ticket run used the deterministic templates and cost
  $0.00. The client, retries, JSON mode, usage accounting and validation are
  covered by mock-transport tests, but real latency, token usage and cost have
  not been measured here. Price defaults are zero until real rates are set.
- **Idempotency is per run.** Keys live in the process and are mirrored only as
  ledger rows; a restart loses them. A persistent key store is the next step.
- **Single-process state.** Runs, approvals and the dataset cache are in memory.
  The JSONL files are atomic, but there is no cross-worker coordination; a
  multi-worker deployment needs a shared store.
- **Data assumptions.** Slash dates are read day-first because the pack is
  day-first; ambiguous dates are flagged. Currency is USD. Attachment handling
  checks file name and content type only, which RP-3.2 permits for this desk.
- **Absent data stays absent.** LF-100223 has no order, lines or shipment in the
  pack and was not synthesised. `UNAVAILABLE` carrier statuses are treated as
  unverifiable, not as "not delivered".
- **Limited labels.** Fifteen of forty tickets carry senior-agent labels. The
  rest are covered by schema, determinism and reconciliation checks, not by an
  outcome label.
- **Next steps.** Run the full set against a live Cadra key and record real
  tokens and cost; add a persistent idempotency store; expand labelled and
  adversarial tickets; replace keyword extraction with a validated advisory
  extraction that is measured against the rule set; and add a real mailbox
  intake adapter. The service should remain proposal-only.
