# DIYA API — Deployment (Render)

This deploys `api/main.py`'s FastAPI backend to a public HTTPS URL so an
external frontend build (e.g. Lovable) can point at something real instead
of `localhost`. `render.yaml` in the repo root is a Render Blueprint that
describes the whole service; deploying is a matter of connecting the GitHub
repo and clicking through Render's UI once.

## One-time setup (click-through steps)

1. Go to <https://dashboard.render.com> and sign in (GitHub login is
   easiest, since Render needs GitHub access anyway).
2. Click **New +** → **Blueprint**.
3. Connect the `Om-5640/diya` GitHub repository (authorize Render's GitHub
   App if this is the first time; grant it access to this repo, or to all
   repos if you'd rather not manage the list).
4. Render detects `render.yaml` at the repo root automatically and shows a
   preview: one service, `diya-api`, plan `free`, Python runtime. Review it
   — it should match what's below — then click **Apply**.
   ```yaml
   services:
     - type: web
       name: diya-api
       runtime: python
       plan: free
       buildCommand: pip install -r requirements.txt
       startCommand: uvicorn api.main:app --host 0.0.0.0 --port $PORT
       healthCheckPath: /api/health
   ```
5. Render clones the repo, runs `pip install -r requirements.txt`, then
   starts `uvicorn api.main:app --host 0.0.0.0 --port $PORT`. First build
   takes a few minutes (installing pandas/numpy/pyarrow/pvlib/highspy from
   wheels — no compilation needed, see the comment in `render.yaml`).
6. Once the deploy shows **Live**, Render gives you a public URL that looks
   like `https://diya-api-XXXX.onrender.com` (the exact subdomain is
   assigned by Render, not chosen in advance).

## What to verify after the first deploy

Replace `<url>` with the real Render URL Render gives you.

- `curl -s <url>/api/health` → `{"ok":true,"phase":4}`
- `curl -s <url>/` → the service pointer JSON (service/docs/openapi/health)
- Open `<url>/docs` in a browser → Swagger UI loads, shows every route
  grouped and documented (this is generated automatically from the same
  Pydantic models as `docs/API_CONTRACT.md` — see that file for the
  human-readable version with real captured examples)
- `curl -s <url>/api/runs` → the 12 precomputed Khavda runs
- `curl -s <url>/api/sites` → the khavda seed site

If all of these work, hand `<url>` to the frontend build (Lovable) as the
API base URL — CORS is already open (`allow_origins=["*"]`, see the comment
in `api/main.py` marking this as temporary) so any frontend origin can call
it.

## Redeploying after a push

Render auto-deploys on every push to the `branch` named in `render.yaml`
(currently `master`) by default — no click-through needed for routine
updates. To redeploy manually (e.g. to force a clean rebuild), open the
`diya-api` service in the Render dashboard and click **Manual Deploy** →
**Deploy latest commit**.

Whenever a route's request/response shape, docstring, or error behavior
changes, re-run `python scripts/generate_api_contract.py` locally and
commit the regenerated `docs/openapi.json` and `docs/API_CONTRACT.md` --
they are generated files, never hand-edited, so they can drift from the
real deployed code if this step is skipped.

## Known, real limitations of this deployment (disclosed, not hidden)

- **Free-tier cold start**: Render's free web services spin down after
  ~15 minutes of no inbound traffic. The first request after idle time can
  take **30-60 seconds** to respond while the container restarts — this is
  a real, expected delay on the free tier, not a bug. A frontend calling
  this API should show a loading state that tolerates this, especially for
  the first request of a session.
- **No persistent disk on the free tier**: `sites/registry.json` ships
  already-seeded in the git repo (the khavda entry), so the API always
  works correctly on a fresh deploy. But any site created at runtime via
  `POST /api/sites` is written to the container's local, ephemeral
  filesystem — it will be **lost** on the next deploy or container
  restart/respin, since the free tier has no persistent disk attached.
  This is fine for demoing the multi-site feature interactively, but not
  yet a durable multi-tenant store; a later phase should move site records
  either onto a Render persistent disk (paid) or an external database.
- **`/api/resolve` is slow by design**: ~60-65 seconds per call (168
  sequential 24-hour MILP solves) — see the comment above that route in
  `api/main.py` and `FREEZE_NOTES.md`. This is unrelated to the deploy
  itself and was already true locally; it's called out here so it isn't
  mistaken for a deployment problem when a frontend integration hits it.
- **CORS is wide open** (`allow_origins=["*"]`) — deliberate for now so a
  Lovable preview's dynamic domain can call the API without pre-registering
  it, but combined with no rate limiting this is a real, temporary
  exposure. Phase G (rate limiting) is what's meant to close this gap.
