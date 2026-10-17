# Deployment (Alternate Clouds)

Alternate Clouds builds the `Dockerfile` at the repository root, so each service tracks its own branch:

| Service | Branch | Root Dockerfile builds | Container port (Settings → Ports) |
|---|---|---|---|
| Talkse | `main` | `talkse/` (Python, FastAPI) | 8000 |
| CRM (Cool Breeze / FlowCRM) | `flowcrm-deploy` | `flowcrm/` (Next.js + Express in one container) | 80 |

`flowcrm-deploy` is `main` plus a root `Dockerfile` copied from `deploy/flowcrm.Dockerfile`
(and `.dockerignore` from `deploy/flowcrm.dockerignore`).

## Variables

Paste into each service's Settings → Variables, **without quotes** around values:

- Talkse: `deploy/talkse.cloud.env`
- CRM: `deploy/flowcrm.cloud.env`

Both files are gitignored (they hold real secrets). The variable names are in `.env.example`.
Key ones:

| Talkse | CRM |
|---|---|
| `PORT=8000`, `DATABASE_URL`, `GROQ_API_KEY`, `DEEPGRAM_API_KEY` | `PORT=80`, `DATABASE_URL`, `DIRECT_URL` |
| `PUBLIC_BASE_URL` (its own URL), `FLOWCRM_URL` | `AUTH_MODE=demo`, `DEMO_ORG_ID=org_coolbreeze`, `DEMO_CLERK_ID=demo_haris`, `WEB_APP_URL` |
| `CRM_ORG_ID`, `CRM_OWNER_USER_ID` | `GROQ_API_KEY`, `TALKSE_MCP_URL` (`<Talkse URL>/mcp/`) |
| `RESEND_API_KEY`, `OWNER_EMAIL`, `HANDOFF_TIMEOUT_SECONDS=90` | `TALKSE_MCP_KEY` (= Talkse's `MCP_API_KEY`) |
| `MCP_API_KEY`, `OWNER_CONSOLE_KEY`, `TELEGRAM_MODE=off` | `RESEND_API_KEY`, `RESEND_FROM_EMAIL` |

Variables override the Dockerfile's `ENV`, so `PORT` must match the Ports setting.

## SignalWire (phone line)

Phone Numbers → +1 201 586 7069 → handler `talkse-webhook-cxml`:
Primary Script URL `<Talkse URL>/voice/incoming` (POST); status callback `<Talkse URL>/voice/status` (POST).

## Updating the CRM branch

```
git checkout flowcrm-deploy
git merge main
copy deploy/flowcrm.Dockerfile Dockerfile      # a merge does not refresh the root copy
copy deploy/flowcrm.dockerignore .dockerignore
git commit -am "flowcrm-deploy: refresh root Dockerfile"   # only if it changed
git push
git checkout main
```

Then Redeploy each service and check its Logs: Talkse prints `Uvicorn running on http://0.0.0.0:8000`,
the CRM prints `FlowCRM API listening on port 4000`.

## If the site shows errors

| Symptom | Likely cause |
|---|---|
| 404 from nginx | No running container behind the URL (deploy stuck or wrong Ports) |
| 502 | App listens on a different port than Settings → Ports |
| Pages load, data calls return 500 | `DATABASE_URL` missing, quoted, or wrong |
| `/mcp` returns 200 without a key | `MCP_API_KEY` not set |
