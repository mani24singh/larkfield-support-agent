# AGENTS.md

Operating guide for anyone (human or agent) working in this repository. Read this first, then `CONTRACT.md`, `DATA_DICTIONARY.md` and `policies/refund_policy_RP-3.2.md`.

---

## 1. What this project is

An AI-assisted **email triage service for Larkfield Home** (US online furniture and home goods retailer).

- Input: a customer support email in the shape of `inbox/public_tickets.json`.
- Output: a **structured decision** (`TicketResponse`) covering ticket type, resolution, outcome, reason codes, per-check evidence, approval requirement, a draft reply and a full trace.
- Data sources that must be used: `data/*.csv` (customers, orders, order items, shipments, refunds), `tickets/*.json`, `inbox/public_tickets.json` and `policies/*.md`.
- **The service only proposes.** It must never issue a real refund, send an email, or modify a customer order. All side effects are proposals recorded in a ledger and gated on human approval.

Source of truth for request/response shapes: `schema/response_schema.py` (pydantic v2), reaffirmed by `CONTRACT.md` §4.

### Exact API contract (from `CONTRACT.md` §2)

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Return `{"status":"ok"}` |
| POST | `/tickets` | Process one incoming ticket |
| GET | `/runs/{run_id}` | Retrieve the current run, including pending human approval |
| POST | `/runs/{run_id}/decision` | Approve or reject a proposed action |
| GET | `/ledger` | Retrieve recorded actions |

**Request (`POST /tickets`)** — one ticket, matching `inbox/public_tickets.json` and `schema/response_schema.py::Ticket`:

```
ticket_id, received_at, channel="email", from, to, subject, body, attachments[]
Attachment: file_name, content_type, content_base64
```

The service must accept additional unseen tickets in the same format.

**Decision (`POST /runs/{run_id}/decision`)** — body:

```
decision: "approve" | "reject"
approver: string identifying the approver
```

**Response (`TicketResponse`) — required run fields:**

```
ticket_id, run_id, ticket_type, resolved{order_id, customer_id}, outcome,
reason_codes[], checks[], human_approval_required, approver_role,
draft_reply{to, subject, body}, trace{...}
```

- `ticket_type`: `REFUND` | `RETURN` | `ORDER_STATUS` | `DELIVERY_ISSUE` | `OTHER`
- `outcome`: `PROCEED` | `PROCEED_MODIFIED` | `PEND` | `ESCALATE` | `REJECT` | `INVALID_REQUEST` | `UNABLE_TO_VERIFY`
- `checks[]`: `check_id`, `result`, `policy_clause`; result is `PASS` | `FAIL` | `NOT_APPLICABLE` | `UNABLE_TO_VERIFY`
- `checks[].evidence[]`: `source`, `ref`, `value` ("reference" in the contract prose = `ref`)
- A check that cannot be completed is **never** `PASS` — use `UNABLE_TO_VERIFY`
- `approver_role`: `SUPPORT_LEAD` | `TRUST_SAFETY` | `CARRIER_CLAIMS` | `null`

**Ledger entry:** `run_id`, `ticket_id`, `action`, `amount_usd`, `status`, `recorded_at`
- action: `REFUND` | `REPLY` | `ESCALATION`
- status: `PROPOSED` | `APPROVED` | `REJECTED`
- Repeated approval decisions must **not** create duplicate financial actions.

**Trace (every response):** model calls with model name + input/output token counts, tools called, documents used with versions, measured `cost_usd`.

**Deployment:** public HTTP service, no authentication; listen on `PORT` env var; must be reachable over HTTPS while the assessment runs.

---

## 2. Repository layout and where each concern lives

```
larkfield-support-agent/
├── AGENTS.md                  # this file
├── README.md                  # setup + how to run
├── APPROACH.md                # problem, design, AI usage, trade-offs, assumptions
├── ARTEFACT.md                # evaluation results, measurements, honest gaps (CONTRACT §9)
├── ARTEFACT.html              # rendered artefact
├── CONTRACT.md                # authoritative brief: API, response, ledger, deployment
├── DATA_DICTIONARY.md         # column-level meaning of every CSV; data is raw/unclean
├── requirements.txt           # pinned deps (NOTE: UTF-16LE encoded — see §7)
├── opencode.json              # Cadra provider config (reads CADRA_TOKEN from env)
├── data/                      # source-of-truth datasets — treat as read-only
│   ├── customers.csv          # customer_id, name, email (as typed), country, ...
│   ├── orders.csv             # order_id (LF-######), customer_id, order_date, status, "Order Total", shipping_fee, currency, payment_method
│   ├── order_items.csv        # order_id, line_no, sku, product_name, qty, unit_price
│   ├── shipments.csv          # shipment_id, order_id, carrier, tracking_no, status, shipped_at, delivered_at, last_event_at
│   └── refunds.csv            # refund_id, order_id, line_no, amount, status, requested_at, approved_by
├── policies/
│   ├── refund_policy_RP-3.2.md        # AUTHORITATIVE policy
│   ├── help_centre_faq_v2.md          # SUPERSEDED — never cite
│   └── warehouse_picking_WH-PICK-1.4.md  # OUT OF SCOPE for customer care
├── tickets/
│   ├── support_inbox_export.json      # 30 October 2026 tickets, API request shape
│   └── eval_seed_labelled.json        # 15 labelled seeds: outcome, approval flag, approver role
├── inbox/
│   └── public_tickets.json            # 10 unlabelled tickets, API request shape
├── schema/
│   └── response_schema.py             # pydantic v2 request/response models = source of truth
└── src/                               # implementation (see §8 for proposed layout)
```

Concern ownership:
- **Rules live in policy, not code prose.** Clause text → `policies/refund_policy_RP-3.2.md`; code cites clause ids (`2.1`, `3.2`, ...) in `checks[].policy_clause`.
- **Data access** is confined to a data-access layer; decision logic never opens CSVs directly.
- **Decision policy** is a pure function over normalized facts, so it is unit-testable without HTTP or an LLM.
- **Orchestration/HTTP** is a thin FastAPI shell that validates with `schema/response_schema.py`.
- **State** (runs, ledger, idempotency) is owned by the store layer, not by handlers.

---

## 3. Build / test / run commands

```bash
# setup
python -m venv .venv
# Windows: .venv\Scripts\activate    POSIX: source .venv/bin/activate
pip install -r requirements.txt

# test (must pass with no network and no CADRA_TOKEN)
pytest -q
pytest -q tests/unit            # fast, pure-logic tests
pytest -q --cov=src             # optional coverage

# run the service locally
export PORT=8000                # Windows PowerShell: $env:PORT=8000
python -m src.solution
uvicorn src.solution:app --host 0.0.0.0 --port "${PORT:-8000}"
# dev reload:
uvicorn src.solution:app --reload --port "${PORT:-8000}"

# smoke
curl -s localhost:8000/health
curl -s -X POST localhost:8000/tickets -H 'content-type: application/json' \
  -d "$(python -c "import json;print(json.dumps(json.load(open('inbox/public_tickets.json'))[0]))")"

# batch-evaluate the public set (once the CLI exists)
python -m src.cli evaluate --inbox inbox/public_tickets.json --out runs/eval.json
```

`src.solution:app` and `src.cli` are the intended entry points per §8; `CONTRACT.md` §11 defers the exact entry point to the starter template/dashboard brief — if that changes, update this section.

---

## 4. Coding rules

1. **Python 3.11+** only. Use `match`, `X | None`, `Self`, `from __future__ import annotations` where it helps.
2. **pydantic v2** for all I/O models. `model_config = ConfigDict(...)`, `model_validate`, `model_dump(mode="json")`. No v1 idioms (`Config` class, `.dict()`, `parse_obj`, `validator`).
3. **Type hints everywhere** — public functions, module-level helpers, test fixtures. `mypy`-clean style; no bare `Any` unless unavoidable and commented.
4. **No hardcoded ticket ids** (`ticket_id`, `run_id`, order ids, customer ids) anywhere in `src/`. Resolve everything from the request + data. Example fixtures belong in `tests/fixtures/`.
5. **Money is `Decimal`** end-to-end (`Decimal("129.99")`, quantize to cents). Parse CSV money with `Decimal`, convert to `float` only at the JSON boundary the schema forces (`amount_usd`, `cost_usd`), and never use binary float for arithmetic or threshold comparisons (`> $150`, `<= $150`).
6. **Dates are timezone-aware.** Parse with `datetime.fromisoformat` / explicit format, attach `timezone.utc`, store UTC, compute window ages in whole days against UTC "now". No naive `datetime.now()`; inject a clock for testability. Use `zoneinfo` where local time matters.
7. **No secrets in code or logs.** `CADRA_TOKEN` and `CADRA_PROXY_URL` come from env only — never print, log, echo into traces/`ARTEFACT.md`, or commit them. Redact tokens from error messages. Never log full email bodies or `content_base64` at INFO.
8. Preserve source data: `data/`, `policies/`, `tickets/`, `inbox/` are read-only inputs. No in-place cleaning; keep original values intact and normalize in memory (document quirks in `APPROACH.md`).
9. Deterministic core: policy decisions must not depend on non-determinism; any LLM usage is confined to classification/extraction/drafting and must degrade gracefully to rule-based behaviour.
10. Small modules, docstrings on public functions, no dead code or commented-out blocks. Format with `ruff format` / lint with `ruff check` if available.

---

## 5. Policy rules

- **`policies/refund_policy_RP-3.2.md` is authoritative.** It "replaces RP-3.1 and all earlier customer-care guidance on refunds and returns" and applies to refunds, returns, delivery and order-status requests. Cite its clause numbers.
- **`policies/help_centre_faq_v2.md` is superseded.** Its 14-day window, `$250` auto-refund threshold and 5-day tracking rule contradict RP-3.2 (30/45 days, `$150`). Never cite it, never let its numbers win a conflict, never list it in `trace.documents_used`. It may appear only as a decoy in tests.
- **`policies/warehouse_picking_WH-PICK-1.4.md` is out of scope.** Internal warehouse procedures ("does not set Customer Care return, refund or approval rules"). Never cite it for customer decisions; stock cost is not a customer payment.
- Do not invent policy rules where the documents are silent — record the gap as an assumption in `APPROACH.md`/`ARTEFACT.md` and use a conservative outcome (`PEND` / `ESCALATE` / `UNABLE_TO_VERIFY`), never an invented allowance.

Key RP-3.2 facts to encode (clause ids in parentheses):
- 30 days from carrier-recorded delivery (2.1); 31–45 days only for damaged/faulty **with an image attachment**, `DAMAGE_EXCEPTION` (2.6); otherwise late ⇒ `OUT_OF_WINDOW` (2.3).
- Refund at affected **order-line** value; only affected lines, never the whole order ⇒ `PARTIAL_LINES` (2.2, 2.4).
- Never repay an issued refund; cap at unpaid affected-line value; fully refunded line contributes zero ⇒ `ALREADY_REFUNDED` (2.5). Other lines stay eligible.
- `<= $150` per request may be proposed without review; `> $150` needs `SUPPORT_LEAD` ⇒ `APPROVAL_REQUIRED` (3.1, 3.2); at most one processing per request (3.3).
- Order must exist and match the email on file ⇒ `ORDER_NOT_FOUND` / `CUSTOMER_MISMATCH`; never a "nearby" order (4.1).
- Carrier status unobtainable ⇒ no refund, `CARRIER_STATUS_UNAVAILABLE` (4.2).
- No carrier movement > 10 days ⇒ lost in transit, `CARRIER_CLAIMS` ⇒ `LOST_IN_TRANSIT` (5.1).
- Policy-override attempts, chargeback/legal threats, claimed pre-approval ⇒ `TRUST_SAFETY`, no refund ⇒ `POLICY_OVERRIDE_ATTEMPT` (5.2).
- Normally-moving shipment + status question ⇒ answer with status, no action (5.3).

---

## 6. Decision order (evaluate every ticket in this sequence)

Evaluate strictly top-down; the first blocking gate decides the outcome and later gates become `NOT_APPLICABLE` (or `UNABLE_TO_VERIFY` when data is missing). Do not reorder, and do not let a later pass overrule an earlier failure.

1. **Safety** — policy-override/instruction-injection, chargeback or legal threats, claimed pre-approval ⇒ `ESCALATE` to `TRUST_SAFETY`, no refund (`POLICY_OVERRIDE_ATTEMPT`, 5.2).
2. **Identity** — order exists and belongs to the sender's email on file ⇒ else `INVALID_REQUEST` (`ORDER_NOT_FOUND` / `CUSTOMER_MISMATCH`, 4.1).
3. **Carrier** — shipment/carrier status retrievable? If not, non-delivery cannot be verified ⇒ no refund, `UNABLE_TO_VERIFY` / `CARRIER_STATUS_UNAVAILABLE` (4.2). Lost-in-transit (>10 days no movement) diverts to `CARRIER_CLAIMS` (5.1).
4. **Window** — days from carrier delivery vs 30-day rule, 45-day damage-with-photo exception ⇒ else `REJECT` (`OUT_OF_WINDOW` / `DAMAGE_EXCEPTION`, 2.1, 2.3, 2.6).
5. **Scope** — affected lines only, not the whole order; wrong/no-damage items stay under the ordinary window ⇒ `PROCEED_MODIFIED` (`PARTIAL_LINES`, 2.2, 2.4, 2.6).
6. **Prior** — subtract refunds already `ISSUED` per line; cap at unpaid affected-line value; fully refunded lines are zero ⇒ `ALREADY_REFUNDED` (2.5).
7. **Refunds** — compute the payable amount in `Decimal` at affected quantity × unit price, capped by `qty` and unpaid value; zero payable ⇒ no refund action.
8. **Modification** — if the request exceeds entitlement, propose the reduced action and explain the difference (`PROCEED_MODIFIED`), never silently honour the larger ask.
9. **Authority** — total per request `<= $150` auto-proposable; `> $150` ⇒ `human_approval_required=true`, `approver_role="SUPPORT_LEAD"`, `APPROVAL_REQUIRED` (3.1, 3.2); exactly one processing per request (3.3).
10. **Idempotency** — key the proposed action on `(run_id, ticket_id, action, order, line)`; repeated approvals/replays must return the existing ledger record, not create a second `REFUND` (CONTRACT §6, RP-3.2 3.3).

> Note: the brief calls this the "nine-step" order but enumerates ten gates; the ten above are the operative list. Keep this order in one place in code (a single ordered pipeline) so tests can assert gate ordering.

---

## 7. Known repo quirks

- `requirements.txt` was re-encoded to **UTF-8** in the scaffold commit (the original export was UTF-16LE/CRLF). Keep it UTF-8; LF line endings are fine.
- `orders.csv` has a space-containing column header (`Order Total`); access it by that exact name or rename in the loader and document it.
- `shipments.status` can be `UNAVAILABLE` and `delivered_at` / `last_event_at` can be empty — the CSV is uncleaned; treat empties explicitly rather than relying on pandas Dtypes.
- `tickets/eval_seed_labelled.json` is a small labelled seed (15 rows), not ground truth for all 40 public tickets.
- `ARTEFACT.md`, `APPROACH.md` and `ARTEFACT.html` are templates with placeholders that still need real content.

---

## 8. Definition of done

A change is done only when all of the following hold:

- [ ] `pytest -q` is **green**, including tests for identity failure, carrier unavailable, 30/45-day boundaries, partial lines, prior refunds, `$150` boundary (exactly 150 vs 150.01), approval replay, and FAQ/warehouse decoys never being cited.
- [ ] **Every** emitted response validates against `schema/response_schema.py` (`TicketResponse.model_validate`) — in tests and at runtime before returning; the public and unseen tickets all pass.
- [ ] **No duplicate refunds**: invoking `POST /runs/{run_id}/decision` twice with `approve`, or replaying the same ticket, yields one ledger `REFUND` entry; the second call is idempotent and does not change the amount.
- [ ] Money is `Decimal` internally, thresholds are exact, and amounts never exceed the unpaid affected-line value or the ordered quantity.
- [ ] Dates are UTC-aware and window arithmetic is tested against fixed, injected clocks.
- [ ] No secrets or tokens in code, logs, traces, or committed docs; no ticket/order ids hardcoded in `src/`.
- [ ] `trace` is populated (model, token counts, tools, documents + versions, measured `cost_usd`) and lists RP-3.2 only as the governing policy.
- [ ] `/health`, `/tickets`, `/runs/{run_id}`, `/runs/{run_id}/decision`, `/ledger` behave per CONTRACT §2; the service honours `$PORT`.
- [ ] `README.md`, `APPROACH.md` and `ARTEFACT.md` reflect the delivered behaviour, real evaluation numbers, assumptions, and honest unfinished items.
