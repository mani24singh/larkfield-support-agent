# Artefact — Larkfield Home refund reconciliation report

Prepared: 10 November 2026. This report covers the 40 public tickets processed by
the support-agent service and reconciles every proposed refund against the
payments ledger and the affected order lines.

## 1. Scope and data sources

The service was evaluated against the supplied data pack, exported on
2 November 2026 and used unmodified.

| Source | Contents | Rows used |
|---|---|---|
| `data/customers.csv` | CRM customers, email on file, loyalty tier | 53 |
| `data/orders.csv` | Order management export (`Order Total` column contains a space) | 49 rows, 47 unique orders |
| `data/order_items.csv` | Order lines, quantity and unit price | 57 lines across 47 orders |
| `data/shipments.csv` | Carrier feed: status, shipped, delivered and last event timestamps | 47 shipments |
| `data/refunds.csv` | Refunds already issued by the payments ledger | 3 |
| `tickets/support_inbox_export.json` | October 2026 support inbox, API request shape | 30 |
| `inbox/public_tickets.json` | Additional public tickets received 2 November 2026 | 10 |
| `tickets/eval_seed_labelled.json` | Senior-agent labels for outcome, approval and approver role | 15 |

Policy documents: **RP-3.2, version 3.2, effective 1 July 2026** (authoritative);
Help Centre FAQ **HC-FAQ, version 2, published 14 October 2025** (superseded);
Warehouse document **WH-PICK-1.4, version 1.4, effective 15 August 2026** (out of
scope for customer care). Response shapes follow the supplied
`schema/response_schema.py`.

## 2. Method

**Normalisation.** Emails are trimmed and lower-cased before matching. Order
references are canonicalised to `LF-######` from the five formats seen in the
inbox (dashed, undashed, hash-prefixed, bare number, and embedded in prose).
Money is parsed to exact two-decimal values from plain, dollar-prefixed and
`USD`-prefixed cells, including thousands separators. Dates accept ISO first,
then day-first slash dates; a slash date that is valid both day-first and
month-first is flagged and the day-first reading is used. Timestamps are stored
in UTC.

**Matching.** The sender's email is matched to the customer record, the order is
resolved by its exact canonical id, and ownership is checked against that
customer. A near-miss or unknown reference is never mapped to a neighbouring
order.

**Policy precedence and decision order.** Every ticket passes the same ordered
gates: safety and policy integrity; identity; carrier status; carrier movement;
delivery window; item scope; prior refunds; refund computation; modification of
over-reaching requests; authority; and idempotency. The first blocking gate
determines the outcome, and later gates are recorded as not applicable rather
than allowed to override it. Model extraction is advisory only: canonical order
ids and all money come from the deterministic layer.

**Evaluation.** All 40 tickets produced schema-valid responses; all 15 labelled
tickets matched their expected outcome, approval requirement, approver role,
amount and reason-code set. Two independent passes produced identical decisions
for 40/40 tickets. A repeated batch produced the same 16 refund actions as the
first pass, with no additional financial rows. The run took 0.57 seconds and
used no model calls (cost $0.0000), so all customer replies came from the
deterministic templates.

## 3. Decision summary

Of the 40 tickets, 28 produced a ledger action: 16 refund proposals, 5 replies
(status answers) and 7 escalations. The remaining 12 produced no action: 5
rejections, 3 invalid requests, 2 unverifiable carrier cases and 2 duplicates.

| Ticket | Type | Resolved order | Outcome | Amount (USD) | Approval | Reason codes |
|---|---|---|---|---|---|---|
| T001 | REFUND | LF-100200 | PEND | 210.00 | Yes — SUPPORT_LEAD | DAMAGE_EXCEPTION, APPROVAL_REQUIRED |
| T002 | REFUND | LF-100201 | PROCEED | 129.00 | No | — |
| T003 | REFUND | LF-100202 | PEND | 185.00 | Yes — SUPPORT_LEAD | APPROVAL_REQUIRED |
| T004 | REFUND | LF-100203 | PROCEED | 45.00 | No | — |
| T005 | REFUND | LF-100204 | PROCEED | 38.00 | No | — |
| T006 | REFUND | LF-100205 | PROCEED | 52.00 | No | — |
| T007 | REFUND | LF-100206 | PROCEED | 52.00 | No | — |
| T008 | DELIVERY_ISSUE | LF-100207 | PROCEED | 0.00 | No | — |
| T009 | DELIVERY_ISSUE | LF-100208 | PROCEED | 0.00 | No | — |
| T010 | DELIVERY_ISSUE | LF-100209 | PROCEED | 0.00 | No | — |
| T011 | ORDER_STATUS | LF-100210 | ESCALATE | 0.00 | Yes — CARRIER_CLAIMS | LOST_IN_TRANSIT |
| T012 | ORDER_STATUS | LF-100211 | ESCALATE | 0.00 | Yes — CARRIER_CLAIMS | LOST_IN_TRANSIT |
| T013 | REFUND | LF-100212 | UNABLE_TO_VERIFY | 0.00 | No | CARRIER_STATUS_UNAVAILABLE |
| T014 | REFUND | LF-100213 | REJECT | 0.00 | No | OUT_OF_WINDOW |
| T015 | REFUND | LF-100214 | PROCEED | 24.00 | No | — |
| T016 | REFUND | LF-100215 | PROCEED | 129.00 | No | — |
| T017 | REFUND | LF-100216 | REJECT | 0.00 | No | OUT_OF_WINDOW |
| T018 | REFUND | LF-100217 | REJECT | 0.00 | No | ALREADY_REFUNDED |
| T019 | REFUND | LF-100218 | PROCEED_MODIFIED | 27.50 | No | PARTIAL_LINES |
| T020 | REFUND | LF-100219 | ESCALATE | 0.00 | Yes — TRUST_SAFETY | POLICY_OVERRIDE_ATTEMPT |
| T021 | RETURN | LF-100220 | ESCALATE | 0.00 | Yes — TRUST_SAFETY | POLICY_OVERRIDE_ATTEMPT |
| T022 | REFUND | — | INVALID_REQUEST | 0.00 | No | ORDER_NOT_FOUND |
| T023 | REFUND | LF-100202 | INVALID_REQUEST | 0.00 | No | CUSTOMER_MISMATCH |
| T024 | DELIVERY_ISSUE | LF-100221 | ESCALATE | 0.00 | Yes — CARRIER_CLAIMS | LOST_IN_TRANSIT |
| T025 | REFUND | LF-100222 | PROCEED_MODIFIED | 45.00 | No | PARTIAL_LINES |
| T026 | REFUND | LF-100224 | PEND | 185.00 | Yes — SUPPORT_LEAD | ALREADY_REFUNDED, APPROVAL_REQUIRED |
| T027 | REFUND | LF-100201 | PROCEED | 0.00 (duplicate) | No | — |
| T028 | REFUND | LF-100204 | PROCEED | 0.00 (duplicate) | No | — |
| T029 | DELIVERY_ISSUE | LF-100208 | PROCEED | 0.00 | No | — |
| T030 | REFUND | LF-100213 | REJECT | 0.00 | No | OUT_OF_WINDOW |
| P01 | REFUND | LF-100225 | PROCEED | 64.00 | No | DAMAGE_EXCEPTION |
| P02 | REFUND | LF-100226 | PEND | 289.00 | Yes — SUPPORT_LEAD | APPROVAL_REQUIRED |
| P03 | DELIVERY_ISSUE | LF-100227 | PROCEED | 0.00 | No | — |
| P04 | REFUND | — | INVALID_REQUEST | 0.00 | No | ORDER_NOT_FOUND |
| P05 | REFUND | LF-100228 | ESCALATE | 0.00 | Yes — TRUST_SAFETY | POLICY_OVERRIDE_ATTEMPT |
| P06 | ORDER_STATUS | LF-100229 | ESCALATE | 0.00 | Yes — CARRIER_CLAIMS | LOST_IN_TRANSIT |
| P07 | REFUND | LF-100230 | PROCEED | 45.00 | No | — |
| P08 | REFUND | LF-100231 | PROCEED_MODIFIED | 38.00 | No | PARTIAL_LINES |
| P09 | REFUND | LF-100232 | UNABLE_TO_VERIFY | 0.00 | No | CARRIER_STATUS_UNAVAILABLE |
| P10 | REFUND | LF-100233 | REJECT | 0.00 | No | OUT_OF_WINDOW |

T027 duplicates T002 and T028 duplicates T005. Their underlying requests would
have produced $129.00 and $38.00 respectively, but the duplicate gate suppressed
the second action, so the proposed amount recorded against them is zero.

## 4. Money reconciliation

Sixteen refund actions were proposed, one per affected line, totalling
**$1,557.50**. No line appears more than once. Every proposed amount equals the
affected quantity multiplied by the unit price, less any refund already issued
for that line.

| Ticket | Order | Line | Item | Affected / ordered qty | Unit price | Affected value | Prior issued | Proposed |
|---|---|---|---|---|---|---|---|---|
| T001 | LF-100200 | 1 | Round wall mirror | 1 / 1 | 210.00 | 210.00 | 0.00 | 210.00 |
| T002 | LF-100201 | 1 | Arc floor lamp | 1 / 1 | 129.00 | 129.00 | 0.00 | 129.00 |
| T003 | LF-100202 | 1 | Oak dining chair | 1 / 1 | 185.00 | 185.00 | 0.00 | 185.00 |
| T004 | LF-100203 | 1 | Glass vase | 1 / 1 | 45.00 | 45.00 | 0.00 | 45.00 |
| T005 | LF-100204 | 1 | Stoneware mug set (4) | 1 / 1 | 38.00 | 38.00 | 0.00 | 38.00 |
| T006 | LF-100205 | 1 | Floating shelf | 1 / 1 | 52.00 | 52.00 | 0.00 | 52.00 |
| T007 | LF-100206 | 1 | Floating shelf | 1 / 1 | 52.00 | 52.00 | 0.00 | 52.00 |
| T015 | LF-100214 | 1 | Candle trio | 1 / 1 | 24.00 | 24.00 | 0.00 | 24.00 |
| T016 | LF-100215 | 1 | Arc floor lamp | 1 / 1 | 129.00 | 129.00 | 0.00 | 129.00 |
| T019 | LF-100218 | 1 | Cushion cover pair | 1 / 1 | 27.50 | 27.50 | 0.00 | 27.50 |
| T025 | LF-100222 | 1 | Glass vase | 1 / 1 | 45.00 | 45.00 | 0.00 | 45.00 |
| T026 | LF-100224 | 2 | Oak dining chair | 1 / 2 | 185.00 | 185.00 | 0.00 | 185.00 |
| P01 | LF-100225 | 1 | Linen throw | 1 / 1 | 64.00 | 64.00 | 0.00 | 64.00 |
| P02 | LF-100226 | 1 | Wool rug 160x230 | 1 / 1 | 289.00 | 289.00 | 0.00 | 289.00 |
| P07 | LF-100230 | 1 | Glass vase | 1 / 1 | 45.00 | 45.00 | 0.00 | 45.00 |
| P08 | LF-100231 | 1 | Stoneware mug set (4) | 1 / 1 | 38.00 | 38.00 | 0.00 | 38.00 |
| **Total** | | | | **16 lines** | | **1,557.50** | **0.00** | **1,557.50** |

Refunds already issued before this run, from `data/refunds.csv`:

| Refund | Order | Line | Amount | Status | Requested |
|---|---|---|---|---|---|
| RF-900 | LF-100217 | 1 | 185.00 | ISSUED | 6 October 2026 |
| RF-901 | LF-100224 | 1 | 45.00 | ISSUED | 10 October 2026 |
| RF-902 | LF-100243 | 1 | 45.00 | ISSUED | 1 November 2026 |
| **Total** | | | **275.00** | | |

**Reconciliation proof.**

- The 16 proposals cover 16 distinct order-line pairs. No pair appears twice.
- The issued lines are LF-100217 line 1, LF-100224 line 1 and LF-100243 line 1.
  The proposed lines are disjoint from all three: T018 (LF-100217) was rejected
  at zero because the line was already refunded in full, and T026 proposes only
  line 2 of LF-100224 because line 1 carries the $45.00 vase credit.
- Every proposed amount is less than or equal to its affected-line value; the
  largest single proposal is $289.00 for one rug, and T026 is capped at one of
  two ordered chairs ($185.00 against $370.00 ordered value).
- Totals reconcile: 16 proposed refunds at $1,557.50 plus 3 previously issued
  refunds at $275.00 gives $1,832.50 of recorded refund activity across the data
  pack, with no overlap between the two sets. Of the proposals, $869.00 (T001,
  T003, T026, P02) is pending support-lead approval and $688.50 is auto-proposable
  under the $150.00 rule.
- Duplicate suppression is visible at the ledger: T027 and T028 each have a
  customer reply saying the request is already being handled, and neither has a
  refund row.

## 5. Exceptions and judgement calls

**Out of window.** T014 (38 days after delivery), T017 (45 days), T030 (38 days)
and P10 (45 days) were all change-of-mind or ordinary requests received after
the 30-day window, with no damage and no photo. They were rejected with
`OUT_OF_WINDOW` and no money was proposed.

**Partial lines and over-reaching requests.** T019 asked for the whole order
after one cushion cover arrived broken; only that line was eligible ($27.50 of a
$316.50 order). T025 asked for the delivery charge as well; the $9.95 shipping
fee was excluded and $45.00 proposed. P08 asked for the whole order after a mug
set arrived broken; $38.00 was proposed against a $327.00 order. All three are
`PROCEED_MODIFIED` with `PARTIAL_LINES`.

**Already refunded.** T018 requested a refund on LF-100217, whose only line was
already refunded in full ($185.00, RF-900); it was rejected at zero with
`ALREADY_REFUNDED`. T026 requested both the vase and one chair from LF-100224;
the vase line carries the $45.00 credit RF-901, so it contributes zero while the
chair line remains eligible, giving `ALREADY_REFUNDED` and `APPROVAL_REQUIRED`
together and a $185.00 proposal.

**Duplicates.** T027 is a forwarded copy of T002 (same sender, order LF-100201,
line 1) and T028 copies T005 (LF-100204, line 1). The idempotency key of
normalised email, order and affected line matched the earlier ticket in the same
run, so no second refund row was created and each duplicate received a reply
explaining that the request is already being handled.

**Carrier unavailable.** T013 (LF-100212) and P09 (LF-100232) have `UNAVAILABLE`
carrier status, so a non-delivery claim cannot be verified. Both are
`UNABLE_TO_VERIFY` with `CARRIER_STATUS_UNAVAILABLE`; no refund was proposed.

**Lost in transit.** T011, T012, T024 and P06 show no carrier movement for more
than ten days. They are escalated to Carrier Claims with `LOST_IN_TRANSIT` and
no refund.

**Injection attempts.** T020, T021 and P05 contain text instructing the assistant
to ignore policy, claiming pre-approval or demanding a specific refund. They are
treated as data, escalated to Trust & Safety with `POLICY_OVERRIDE_ATTEMPT`, and
no money is proposed. The same result was verified for an injection embedded in
a forwarded thread appended to an otherwise valid request.

**Status answers.** T008, T009, T010, T029 and P03 are moving parcels with normal
tracking; they receive a status reply and no financial action.

## 6. Policy precedence note

RP-3.2 is the governing document. It is the current customer-care policy
(version 3.2, effective 1 July 2026) and states that it replaces RP-3.1 and all
earlier customer-care guidance on refunds and returns. It sets the 30-day
window, the 45-day damage-with-photo exception, affected-line refunding,
deduction of prior refunds, the $150.00 approval threshold and the escalation
rules applied here.

The Help Centre FAQ (HC-FAQ version 2) is marketing content published in October
2025, not a policy. Its 14-day return window, $250 automatic-refund threshold
and five-day tracking rule directly contradict RP-3.2. It is therefore treated
as superseded and was not cited in any check or trace.

The warehouse picking document (WH-PICK-1.4) governs internal fulfilment and
explicitly says it does not set customer-care rules. Its stock-cost language was
not used, and stock cost is not a customer payment.

Where the documents are silent, no rule was invented: the service defaults to a
conservative outcome and records the assumption rather than granting a refund.

## 7. Data quality log

| Issue | Evidence | Handling |
|---|---|---|
| Duplicate order rows | LF-100216 and LF-100239 appear twice in `orders.csv` with identical values | Collapsed to one record, each collapse logged; a conflicting duplicate would abort the load |
| Mixed date formats | ISO dates plus `26/08/2026`, `08/10/2026`, `06/10/2026` | ISO first, then day-first, then month-first; day-first preferred |
| Ambiguous dates | `08/10/2026` (×2) and `06/10/2026` parse both ways | Flagged as ambiguous, resolved day-first, and carried as evidence |
| Mixed money formats | `223.00`, `$129.00`, `USD 24.00`, thousands separators | Parsed to exact two-decimal values; no floating-point arithmetic |
| Messy email casing and spacing | Senders such as `  ASHA.SHAH0@MAIL.EXAMPLE ` | Trimmed and lower-cased before matching |
| Varied order references | `LF-100200`, `LF100200`, `#100200`, `100200`, `order order 100200` | All canonicalised to `LF-######`; a seven-digit near miss is rejected rather than guessed |
| Blank carrier timestamps | Unshipped or unavailable shipments have empty `delivered_at` / `last_event_at` | Treated as absent, never as a date; the window cannot start without a delivery scan |
| `UNAVAILABLE` carrier status | LF-100212, LF-100232, LF-100238 | Non-delivery claims marked unverifiable, no refund |
| Scan older than shipment | LF-100210, LF-100211, LF-100229, LF-100239 have `last_event_at` before `shipped_at` | Treated as a stale feed: the old scan is used for the ten-day movement test, producing lost-in-transit escalations |
| Order absent from items and shipments | LF-100223 is missing from both files | Treated as a genuinely absent order; no synthetic line was created |
| Issued refund predating the ticket batch | RF-902 on LF-100243, issued 1 November 2026 | Reported as already issued; no ticket requests that line |
| Template placeholders | Project docs were scaffold stubs | Replaced by this report and the measured evaluation output |

## 8. Limitations and next steps

The evaluation is rule-based: no model credentials were configured, so every
customer reply came from the deterministic templates and the recorded model cost
is zero. The Cadra client, extraction prompt and reply validation are
implemented and unit-tested against a mock provider, but a live-provider run
with real token counts and cost has not been measured.

The labelled seed contains 15 of the 40 public tickets; the other 25 are covered
by schema, determinism and reconciliation checks but not by a senior-agent
outcome label. Decisions on loosely worded emails depend on keyword extraction
for products and quantities, which is deliberately conservative and could be
improved with more labelled examples.

Slash dates are assumed day-first. This matches the data pack, and ambiguous
values are flagged, but a customer writing month-first could still be
misread by a day. Attachment handling checks file name or content type only;
image content is not analysed, which RP-3.2 explicitly permits for this desk.

Idempotency is scoped to a run: state is held in process and mirrored to atomic
JSONL files under `runs/<run_id>`. A restart loses in-memory duplicate keys, so a
cross-run idempotency store is the next hardening step. The service also remains
proposal-only by design: it never issues a refund, sends an email or changes an
order.

Planned next steps are a live Cadra evaluation with measured tokens and cost,
more labelled tickets and adversarial emails, a persistent key-value store for
refunds that survives restarts, and an intake adapter so real mailbox events can
be submitted without hand-built JSON.
