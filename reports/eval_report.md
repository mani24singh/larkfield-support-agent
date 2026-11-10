# Evaluation report

Generated: 2026-11-10T17:21:08+00:00
Run: `run_7a80571e0d904bfa8b7d00d7f799fd72` (replay `run_24e264321e7c417cbae17d9bd7a0674b`)
Runtime: 0.59s

## Headline metrics

| Metric | Result |
|---|---|
| Tickets processed | 40 / 40 |
| Schema-valid responses | 40 / 40 |
| Labelled tickets matched | 15 / 15 |
| Deterministic across two passes | 40 / 40 |
| Idempotent replay | PASS |
| Refund actions (single / replay) | 16 / 16 |
| Expected duplicate replies | 18 |
| Model cost (USD) | 0.0000 |
| Overall | PASS |

### Outcome distribution

| Outcome | Count |
|---|---|
| ESCALATE | 7 |
| INVALID_REQUEST | 3 |
| PEND | 4 |
| PROCEED | 16 |
| PROCEED_MODIFIED | 3 |
| REJECT | 5 |
| UNABLE_TO_VERIFY | 2 |

## Failures

None.

## Per-ticket results

| Ticket | Type | Outcome | Approval | Approver | Amount (USD) | Reason codes | Label |
|---|---|---|---|---|---|---|---|
| T001 | REFUND | PEND | yes | SUPPORT_LEAD | 210.00 | DAMAGE_EXCEPTION, APPROVAL_REQUIRED | PASS |
| T002 | REFUND | PROCEED | no | — | 129.00 | — | PASS |
| T003 | REFUND | PEND | yes | SUPPORT_LEAD | 185.00 | APPROVAL_REQUIRED | PASS |
| T004 | REFUND | PROCEED | no | — | 45.00 | — | PASS |
| T005 | REFUND | PROCEED | no | — | 38.00 | — | PASS |
| T006 | REFUND | PROCEED | no | — | 52.00 | — | PASS |
| T007 | REFUND | PROCEED | no | — | 52.00 | — | — |
| T008 | DELIVERY_ISSUE | PROCEED | no | — | — | — | PASS |
| T009 | DELIVERY_ISSUE | PROCEED | no | — | — | — | — |
| T010 | DELIVERY_ISSUE | PROCEED | no | — | — | — | — |
| T011 | ORDER_STATUS | ESCALATE | yes | CARRIER_CLAIMS | — | LOST_IN_TRANSIT | PASS |
| T012 | ORDER_STATUS | ESCALATE | yes | CARRIER_CLAIMS | — | LOST_IN_TRANSIT | — |
| T013 | REFUND | UNABLE_TO_VERIFY | no | — | — | CARRIER_STATUS_UNAVAILABLE | PASS |
| T014 | REFUND | REJECT | no | — | — | OUT_OF_WINDOW | PASS |
| T015 | REFUND | PROCEED | no | — | 24.00 | — | — |
| T016 | REFUND | PROCEED | no | — | 129.00 | — | — |
| T017 | REFUND | REJECT | no | — | — | OUT_OF_WINDOW | — |
| T018 | REFUND | REJECT | no | — | — | ALREADY_REFUNDED | PASS |
| T019 | REFUND | PROCEED_MODIFIED | no | — | 27.50 | PARTIAL_LINES | PASS |
| T020 | REFUND | ESCALATE | yes | TRUST_SAFETY | — | POLICY_OVERRIDE_ATTEMPT | PASS |
| T021 | RETURN | ESCALATE | yes | TRUST_SAFETY | — | POLICY_OVERRIDE_ATTEMPT | PASS |
| T022 | REFUND | INVALID_REQUEST | no | — | — | ORDER_NOT_FOUND | — |
| T023 | REFUND | INVALID_REQUEST | no | — | — | CUSTOMER_MISMATCH | — |
| T024 | DELIVERY_ISSUE | ESCALATE | yes | CARRIER_CLAIMS | — | LOST_IN_TRANSIT | — |
| T025 | REFUND | PROCEED_MODIFIED | no | — | 45.00 | PARTIAL_LINES | — |
| T026 | REFUND | PEND | yes | SUPPORT_LEAD | 185.00 | ALREADY_REFUNDED, APPROVAL_REQUIRED | PASS |
| T027 | REFUND | PROCEED | no | — | 129.00 | — | — |
| T028 | REFUND | PROCEED | no | — | 38.00 | — | — |
| T029 | DELIVERY_ISSUE | PROCEED | no | — | — | — | — |
| T030 | REFUND | REJECT | no | — | — | OUT_OF_WINDOW | — |
| P01 | REFUND | PROCEED | no | — | 64.00 | DAMAGE_EXCEPTION | — |
| P02 | REFUND | PEND | yes | SUPPORT_LEAD | 289.00 | APPROVAL_REQUIRED | — |
| P03 | DELIVERY_ISSUE | PROCEED | no | — | — | — | — |
| P04 | REFUND | INVALID_REQUEST | no | — | — | ORDER_NOT_FOUND | — |
| P05 | REFUND | ESCALATE | yes | TRUST_SAFETY | — | POLICY_OVERRIDE_ATTEMPT | — |
| P06 | ORDER_STATUS | ESCALATE | yes | CARRIER_CLAIMS | — | LOST_IN_TRANSIT | — |
| P07 | REFUND | PROCEED | no | — | 45.00 | — | — |
| P08 | REFUND | PROCEED_MODIFIED | no | — | 38.00 | PARTIAL_LINES | — |
| P09 | REFUND | UNABLE_TO_VERIFY | no | — | — | CARRIER_STATUS_UNAVAILABLE | — |
| P10 | REFUND | REJECT | no | — | — | OUT_OF_WINDOW | — |
