# F1 Capstone Template

Starter repository for the Cadra F1 walking-skeleton capstone. Use OpenCode with the Cadra provider to build your solution, document your approach, and submit a public GitHub repo for evaluation.

## Prerequisites

- Python 3.11+
- [OpenCode](https://opencode.ai) ≥ 1.17.0
- A Cadra JWT (`CADRA_TOKEN`) from the F1 Setup page
- Your Cadra proxy URL (from the F1 Setup page)

## Setup

1. Clone this repo (or use it as a GitHub template).
2. Set the environment variables (both values come from the F1 Setup page):
   ```bash
   export CADRA_PROXY_URL=<your-proxy-url>   # e.g. https://your-proxy.example.com/v1
   export CADRA_TOKEN=<your-cadra-jwt>
   ```
   `opencode.json` reads both via `{env:…}` — no file edits needed.
3. Install OpenCode if not already installed (see [opencode.ai](https://opencode.ai)).
4. Run OpenCode in this directory:
   ```bash
   opencode
   ```
5. Complete `APPROACH.md` and implement your solution in `src/` (start with `src/solution.py`).
6. Push your work to a **public** GitHub repository.
7. Submit your repo URL on the F1 demo page.

## Python environment (optional)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run the service

```bash
export PORT=8000
uvicorn src.solution:app --host 0.0.0.0 --port "${PORT:-8000}"
# or: python -m src.solution
```

Health probes: `GET /health` and `GET /healthz` both return `{"status":"ok"}`.
Interactive API docs are at `/docs`. Submit one ticket with:

```bash
curl -s -X POST localhost:8000/tickets \
  -H 'content-type: application/json' \
  -d "$(python -c "import json;print(json.dumps(json.load(open('inbox/public_tickets.json'))[0]))")"
```

## Deploy

The container is self-contained: Python 3.11 slim, non-root `app` user, uvicorn
bound to `0.0.0.0:$PORT`. No secrets are baked into the image — provide
`CADRA_TOKEN` (and the rest) as platform environment variables. `PORT` is
injected by the platform; do not hard-code it in production.

```bash
docker build -t larkfield-support-agent .
docker run --rm -p 8000:8000 \
  -e CADRA_TOKEN=<your-cadra-jwt> \
  -e CADRA_PROXY_URL=<your-proxy-url> \
  larkfield-support-agent
curl -s localhost:8000/healthz     # {"status":"ok"}
```

Start command (any platform that can run uvicorn directly):

```bash
uvicorn src.solution:app --host 0.0.0.0 --port $PORT
```

### Render

1. Push this repository to GitHub.
2. In the Render dashboard choose **New + → Web Service** and connect the repo.
3. Set **Language** to `Docker`. **Dockerfile Path** `./Dockerfile`, **Docker
   Build Context** `.` (repository root).
4. Set **Health Check Path** to `/healthz`.
5. Open **Environment** and add:
   - `CADRA_PROXY_URL` — your Cadra proxy base URL (e.g. `https://…/v1`)
   - `CADRA_TOKEN` — your Cadra JWT (mark it as a secret)
   - `CADRA_MODEL` — `cadra/cadra`
   - `REFUND_AUTO_APPROVE_LIMIT_USD=150.00`
   - `RETURN_WINDOW_DAYS=30`, `DAMAGE_WINDOW_DAYS=45`, `LOST_IN_TRANSIT_DAYS=10`
   - optional: `CADRA_INPUT_COST_PER_MTOK`, `CADRA_OUTPUT_COST_PER_MTOK`
   - do **not** set `PORT`; Render injects it and the Dockerfile honours it.
6. Click **Create Web Service**, wait for the health check to pass, then:
   ```bash
   curl -s https://<your-service>.onrender.com/healthz
   ```

### Railway

1. Push this repository to GitHub.
2. Create a project: **New Project → Deploy from GitHub repo**, or from the CLI
   run `railway login` then `railway init` in the repository root and
   `railway up`.
3. Railway detects the `Dockerfile` automatically. If it does not, open
   **Settings → Build** and set the Dockerfile path to `Dockerfile`.
4. Open **Variables** and add the same variables as above (`CADRA_PROXY_URL`,
   `CADRA_TOKEN`, `CADRA_MODEL`, the policy thresholds, optional price table).
   Do **not** set `PORT`; Railway injects it and the start command uses it.
5. Under **Settings → Networking**, click **Generate Domain**. Railway routes
   to the container's `$PORT` automatically.
6. After the deployment is live:
   ```bash
   curl -s https://<your-domain>.up.railway.app/healthz
   ```

Both platforms keep the endpoint public and unauthenticated, as the brief
requires. Rotate `CADRA_TOKEN` in the platform dashboard if it is ever exposed.

## Project layout

```
.
├── opencode.json    # Cadra provider config (reads CADRA_PROXY_URL + CADRA_TOKEN from env)
├── APPROACH.md      # Your written approach (required for submission)
├── src/
│   └── solution.py  # Your solution code
├── requirements.txt
└── README.md
```
