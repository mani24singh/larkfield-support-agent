# Larkfield Home support agent

A FastAPI service that reads a Larkfield Home customer support email and returns
a structured, schema-valid decision: ticket type, resolved order and customer,
outcome, reason codes, per-check evidence, approval requirement, draft reply and
trace.

It is proposal-only. It never issues a refund, sends an email or changes an
order; financial actions are recorded in an append-only ledger and wait for a
human decision where RP-3.2 requires one.

## What it does

- Matches the sender and order against the supplied CRM, order, shipment and
  refund data. Order references in five common formats resolve to `LF-######`;
  an unknown or near-miss reference is never mapped to a nearby order.
- Applies **RP-3.2** (version 3.2) in a fixed order: safety, identity, carrier,
  delivery window, affected lines, prior refunds, refund amount, modification,
  authority and idempotency.
- Refunds only affected lines, at affected quantity × unit price, in `Decimal`,
  excluding shipping, deducting refunds already `ISSUED`, and pends anything
  above $150.00 for a Support Lead. Exactly $150.00 proceeds.
- Escalates policy-override attempts to Trust & Safety and lost-in-transit
  parcels to Carrier Claims; treats unverifiable carrier statuses as
  unverifiable rather than as a denial.
- Writes responses and ledger rows atomically under `runs/<run_id>/`.
- Uses an OpenAI-compatible Cadra model **only** for advisory extraction and
  reply wording. With no key configured it writes replies from deterministic
  templates; either way the decision and money never change.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export PORT=8000                 # Windows PowerShell: $env:PORT=8000
uvicorn src.solution:app --host 0.0.0.0 --port "${PORT:-8000}"
```

The application instance is exported directly by `src/solution.py` as `app`; it
is built by the `create_app()` factory in `src/api.py`. `python -m src.solution`
starts the same instance.

```bash
curl -s localhost:8000/health
# {"status":"ok"}
```

Configuration is environment-only. Copy `.env.example` for the full list; no
secrets are committed. `CADRA_TOKEN` and `CADRA_PROXY_URL` are optional and only
enable model-drafted replies. Policy thresholds, data paths, run directory and
the per-million-token price table are all settable there.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health`, `/healthz` | Liveness: `{"status":"ok"}` |
| POST | `/tickets` | One ticket object or a list; returns a list of `TicketResponse` |
| GET | `/runs/{run_id}` | Responses and ledger for a run |
| POST | `/approvals` | Approve or reject a pending action |
| POST | `/runs/{run_id}/decision` | Alias for `/approvals` with `run_id` in the path |
| GET | `/ledger?run_id=` | Ledger entries for a run (all runs if omitted) |
| GET | `/docs` | Interactive OpenAPI documentation |

Submit one ticket:

```bash
curl -s -X POST localhost:8000/tickets \
  -H 'content-type: application/json' \
  -d "$(python -c "import json;print(json.dumps(json.load(open('inbox/public_tickets.json'))[0]))")"
```

Submit a batch (one run id for the whole batch, processed in input order):

```bash
curl -s -X POST localhost:8000/tickets \
  -H 'content-type: application/json' \
  -d @inbox/public_tickets.json
```

Inspect the run:

```bash
curl -s localhost:8000/runs/<run_id>
curl -s "localhost:8000/ledger?run_id=<run_id>"
```

Approve or reject. Only `PEND` and `ESCALATE` tickets are approvable; repeating
the same decision returns 200 without creating a second action; the opposite
decision or a non-approvable outcome returns 409.

```bash
curl -s -X POST "localhost:8000/approvals?run_id=<run_id>&ticket_id=T003" \
  -H 'content-type: application/json' \
  -d '{"decision":"approve","approver":"lead@larkfield.example"}'
```

## Tests and evaluation

```bash
pytest -q
```

The suite covers ingest normalisation and duplicate collapsing, policy gates,
money and authority boundaries, injection and duplicate handling, schema
validation of all 40 public tickets, determinism and the HTTP API.

```bash
python eval/run_eval.py
```

The evaluation runs all 40 public tickets, validates every response against
`schema/response_schema.py`, compares the 15 labelled tickets from
`tickets/eval_seed_labelled.json`, checks determinism across two passes and
replays the batch for refund idempotency. It writes `reports/eval_report.md`
with headline metrics, a failure list and a per-ticket table, and exits non-zero
if anything fails. The reconciliation of every proposed refund is in
`ARTEFACT.md`.

## Deployment

Docker:

```bash
docker build -t larkfield-support-agent .
docker run --rm -p 8000:8000 \
  -e CADRA_TOKEN=<your-cadra-jwt> \
  -e CADRA_PROXY_URL=<your-proxy-url> \
  larkfield-support-agent
curl -s localhost:8000/healthz
```

The image is Python 3.11 slim, runs as an unprivileged `app` user, reads all
configuration from the environment, and starts uvicorn on `$PORT`. The portable
start command is:

```bash
uvicorn src.solution:app --host 0.0.0.0 --port $PORT
```

**Render.** Push to GitHub, create a **Web Service**, choose **Docker**, set
**Dockerfile Path** `./Dockerfile` and **Build Context** `.`, set **Health Check
Path** `/healthz`, and add `CADRA_PROXY_URL`, `CADRA_TOKEN` (as a secret),
`CADRA_MODEL` and the policy thresholds as environment variables. Do not set
`PORT`; Render injects it. Deploy and check
`https://<service>.onrender.com/healthz`. Keep the Root Directory at the
repository root.

**Railway.** Create a project from the repo or run `railway up`; Railway detects
the Dockerfile. Add the same variables under **Variables** (not `PORT`), then
**Settings → Networking → Generate Domain** and check
`https://<domain>.up.railway.app/healthz`.

Both platforms must build the current `main`. `src/solution.py` exports the
single FastAPI instance as `app`; an `Attribute "app" not found` error means the
platform is building an older commit whose stub only defined `main()`.

## Results

From `reports/eval_report.md` on 10 November 2026:

- 40/40 responses are schema-valid.
- 15/15 labelled tickets match the senior-agent outcome, approval, approver
  role, amount and reason-code set.
- 40/40 decisions are identical across two independent passes.
- Replay is idempotent: 16 refund actions on the first pass and 16 on the
  repeat; duplicates T027/T028 produce no second action.
- 16 refunds totalling $1,557.50 were proposed, with no line refunded twice and
  no amount above its affected value (`ARTEFACT.md` §4).
- The run took about 0.6 seconds and used $0.0000 of model cost because no Cadra
  key was configured; replies came from the deterministic templates.

The evaluation is rule-based on 40 public tickets, 15 of which are labelled. A
live-model run and a larger labelled set are the main outstanding measurements.
