# Repository audit: Talkse Live + FlowCRM

**Date:** 17 October 2026 · **Scope:** whole repo (`talkse/`, `flowcrm/`, `deploy/`, `scripts/`), including uncommitted MCP work
**Method:** code review of every Talkse module and the FlowCRM files we changed, plus checks run against the code (keyword tests, dependency checks, test runs, live probes)

## Status update (17 October, afternoon)

| ID | Status | What changed |
|---|---|---|
| C1 | Fixed in code | FlowCRM image listens on port 80; applies on the next deploy (`deploy/flowcrm.cloud.env`) |
| C2 | **Fixed** | `/mcp` requires `MCP_API_KEY` (401 without it); FlowCRM sends `TALKSE_MCP_KEY` |
| C3 | **Fixed** | `owner_approved` removed; jobs needing approval create an owner request; booking needs Haris's approval for that exact job; the assistant can't answer approvals |
| H1 | Fixed in config | `OWNER_CONSOLE_KEY` set in the Talkse cloud variables |
| H2 | **Fixed** | Web callers are asked for a phone number (spoken digits understood, can decline); phone calls use caller ID; saved on the call, appointment and CRM contact |
| H3 | Open | Rotate all shared keys after the event (now also Resend) |
| H4 | Accepted | Demo mode only; never with real data |
| H5 | **Fixed** | Per-IP rate limits: 20 new calls/hour, 120 turns/10 min |
| H6 | **Fixed** | Background sweeper finalizes abandoned calls every minute (call-back task, CRM lead, Inbox notification) |
| H7 | Partly fixed | 17 unit tests in `talkse/tests` (parsing, read-back, keywords, time) |
| M1 | **Fixed** | Unparseable times are asked again, never guessed |
| M2 | **Fixed** | Booking is read back and needs a "yes"; changes keep the parts not changed |
| M3 | **Fixed** | Connection pool: ~2.5 s per phone turn (was 5–7 s) |
| M7 / M8 | **Fixed** | Whole-word keyword matching; beeping smoke/CO detector isn't an emergency; "home office" / "at the store" aren't commercial |
| M9 | Improved | Contacts now usually have a phone number to match on |
| M10 | Improved | Talkse emits FlowCRM outbox events, so automation runs on its leads and bookings |
| — | **Fixed** | `TASK_OVERDUE` trigger now fires (poller); `ACTIVITY_LOGGED` hidden (nothing emits it) |
| M11, M12, L1–L10 | Open | After the event |

## Summary

The product works end to end locally: voice booking, owner handoff (console + Telegram), safety alerts, FAQ learning, CRM sync, and the FlowCRM assistant over MCP. The biggest risks are not in the core logic but around it:

1. **Everything is public with no authentication**: the owner console, the MCP server and the FlowCRM API. Anyone with a URL can approve jobs, read transcripts or book visits.
2. **The human-in-the-loop can be bypassed** through the MCP `book_visit` tool (`owner_approved=true`).
3. **No phone number is collected**, yet the AI promises "Haris will call you back".
4. **FlowCRM is down in production** because of a port mismatch (`PORT=3000` variable vs. platform routing to 80).

| Severity | Count | Fix before the demo? |
|---|---|---|
| Critical | 3 | Yes |
| High | 7 | Yes for H1–H3; others if time allows |
| Medium | 12 | Optional |
| Low | 10 | After the event |

## Completion by component

| Component | Status | Notes |
|---|---|---|
| Talkse voice pipeline (STT, intent, booking, TTS) | Complete | Tested locally; deployed version runs older code until rebuilt |
| Owner handoff (approval, questions, timeout) | Complete | Owner console works; Telegram tested in simulation only (campus network blocks Telegram) |
| Safety alerts | Complete | Keyword false positives (M7) |
| FAQ learning loop | Complete | |
| CRM sync (Talkse → FlowCRM) | Complete | Bypasses FlowCRM's outbox events (M10) |
| FlowCRM demo mode | Complete | Down in production (C1) |
| FlowCRM AI Assistant over MCP | Complete locally, **uncommitted** | Needs `GROQ_API_KEY` + `TALKSE_MCP_URL` on the FlowCRM service |
| New call page (3D orb) | Complete | Not visually reviewed on a real phone yet |
| Automated tests | Missing | No Talkse tests; FlowCRM tests need a real DB (H7) |
| Production hardening (auth, rate limits, monitoring) | Not started | See Security |

## Critical

| ID | Area | Issue | Where | Impact | Fix |
|---|---|---|---|---|---|
| C1 | Deployment | **FlowCRM listens on 3000, platform routes to 80** | FlowCRM service Variables (`PORT=3000`) vs. Settings → Ports (80) | FlowCRM is down (502) | Set `PORT=80` (or delete it) → Redeploy. 1 min |
| C2 | Security | **MCP server is public and unauthenticated**, DNS-rebinding protection disabled | `talkse/mcp_server.py` (mounted at `/mcp`) | Anyone can book visits, read call transcripts (customer PII), answer owner requests, add FAQs the AI will repeat to callers | Require a shared secret header (`MCP_API_KEY`) checked in a small middleware; send it from `talkse-mcp-client.ts` |
| C3 | Security / theme | **Owner approval can be bypassed** via MCP `book_visit(owner_approved=true)` | `talkse/mcp_server.py` `book_visit` | Breaks the core "Human + AI" promise: an LLM or attacker can book installs/commercial jobs without Haris | Remove the `owner_approved` parameter; if approval is needed, create an owner request (escalation) instead of booking |

## High

| ID | Area | Issue | Where | Impact | Fix |
|---|---|---|---|---|---|
| H1 | Security | **Owner console + owner API have no auth by default** (`OWNER_CONSOLE_KEY` optional) | `talkse/server.py` `require_owner`, `/owner` | Anyone with the URL can approve/deny jobs, read summaries, save FAQs | Set `OWNER_CONSOLE_KEY` on the Talkse service and open `/owner?key=...` |
| H2 | Product gap | **Caller phone number is never collected** | `call_engine.py` (`required_map`), `call.html` | Every callback promise ("Haris will call you back") is impossible to fulfil; CRM contacts have no phone | Add `caller_phone` to the booking fields (ask "What's the best number to reach you?"), store it on the call |
| H3 | Security / ops | **Secrets were shared in chat and screenshots**: Neon password, Groq key, Deepgram key, Telegram bot token | — | Account misuse, database access | Rotate all four after the closing ceremony (Neon reset password, new Groq/Deepgram keys, BotFather `/revoke`) |
| H4 | Security | **FlowCRM API is fully open in demo mode** and CORS allows every origin with credentials | `flowcrm/apps/api/src/middleware/auth.ts` (demo), `app.ts` CORS callback | Anyone can read or modify all CRM data via the API | Acceptable for a public demo; never run `AUTH_MODE=demo` with real data; restrict CORS to `WEB_APP_URL` |
| H5 | Cost / abuse | **No rate limiting or auth on `/api/calls` and `/turn`** | `talkse/server.py` | Bots can burn Groq/Deepgram credits and fill the database | Add a per-IP limit (e.g. `slowapi`) and a max turns/calls per IP per hour |
| H6 | Reliability | **Abandoned calls are never finalized**: if the caller closes the tab mid-call or while on hold, the call stays `collecting` / `waiting_human` forever | `handoff.check_call` runs only when the page polls; CRM sync only on terminal status | Lost leads; pending escalations never time out; owner sees stale requests | Background sweeper (every 60 s): time out stale escalations, mark calls idle >10 min as ended, run CRM sync |
| H7 | Testing | **No automated tests for Talkse**; FlowCRM's tests require `DATABASE_URL` and run against the real database | `talkse/` (no tests), `flowcrm/apps/api/tests/jest.setup.ts` | Regressions go unnoticed; running FlowCRM tests would write into the shared demo DB | Use a separate Neon branch for tests; add pytest for `call_engine`, `handoff`, `booking_engine.parse_time_string` (the scenario scripts already written are a starting point) |

## Medium

| ID | Area | Issue | Where | Fix |
|---|---|---|---|---|
| M1 | Correctness | Unparseable time silently becomes **"tomorrow 2 PM"** and gets booked | `booking_engine.parse_time_string` | Return `None` and ask the caller again |
| M2 | UX / correctness | **No confirmation before booking**: the AI books as soon as all fields are present | `call_engine._execute_action` | Read back "AC repair, Round Rock, Thursday 10 AM, Sarah, correct?" before booking |
| M3 | Performance | **New DB connection per query, no pooling**; a turn opens ~6 connections (6–16 s per turn observed locally) | `talkse/db.py` `get_connection` | `psycopg2.pool.ThreadedConnectionPool` (or psycopg 3 pool) |
| M4 | Performance | 2 sequential LLM calls for questions (intent + FAQ match) | `call_engine.handle_turn`, `faq.py` | Cache FAQ matches; or merge FAQ matching into the intent prompt |
| M5 | Performance | FlowCRM polls **every query every 5 s** in demo mode | `QueryProvider.tsx` | 15–30 s, or poll only Dashboard/Pipeline/Leads |
| M6 | Rate limits | Groq free tier (8k tokens/min) is easily hit by the assistant with ~24 tool schemas | `groq-chat-client.ts` | Retry added; also send only relevant tools, or upgrade the tier |
| M7 | Correctness | **Safety keyword false positives**: "my smoke detector is beeping" triggers an emergency | `company_data.json` `emergency_protocol.keywords` | Word-boundary matching + an LLM confirmation for borderline phrases ("smoke detector", "smoking hot") |
| M8 | Correctness | **Approval keyword false positives**: "home office", "bought it at the store" trigger an owner handoff | `booking_engine.check_owner_approval` | Match keywords only in the job description/area, not the whole transcript; word boundaries |
| M9 | Data quality | Contacts are matched **by name only** when no phone is known; two different "John Smith"s merge | `crm_sync.upsert_contact` | Match on phone (after H2), then email |
| M10 | Architecture | `crm_sync` writes FlowCRM tables with **raw SQL**, bypassing FlowCRM's service layer and **outbox events**, so FlowCRM automations never fire for Talkse leads; schema changes in Prisma silently break it | `talkse/crm_sync.py` | Call a FlowCRM API endpoint (or emit `OutboxEvent` rows) instead of direct inserts |
| M11 | Concurrency | `handle_turn` reads, modifies and writes `state_json` without a lock; two overlapping requests for the same call can overwrite each other | `call_engine.handle_turn` | `SELECT ... FOR UPDATE` in a transaction, or a per-call lock |
| M12 | Deployment | **Two copies of the FlowCRM Dockerfile** (`deploy/flowcrm.Dockerfile` and the `flowcrm-deploy` root copy); merges don't refresh the copy (already caused one bug) | `deploy/`, `flowcrm-deploy` branch | Platform supports Root directory only at creation: create a fresh FlowCRM service with Root directory `flowcrm` once, then delete the branch |

## Low

| ID | Area | Issue | Fix |
|---|---|---|---|
| L1 | Ops | Customer names and summaries are written to logs (`OWNER HANDOFF ...`) | Log IDs only |
| L2 | Ops | Telegram polling breaks with more than one instance (409 conflicts) | Use webhook mode when scaling |
| L3 | Ops | Containers run as root; no health checks configured on the platform | Non-root user; enable the platform health probe on `/health` |
| L4 | Data | `talkse` schema uses `CREATE TABLE IF NOT EXISTS` only, no migrations | Add a migrations tool (e.g. a numbered SQL folder) before changing columns |
| L5 | Correctness | Whisper can hallucinate short phrases ("Thank you.") on silent recordings | Reject transcripts under ~2 words when the recording is near-silent |
| L6 | FlowCRM | AI Assistant tools `score_lead` / `draft_email`, and the Inbox/Documents pages, need services that aren't deployed | Hide or label them in demo mode |
| L7 | Repo | Internal plan PDF (`docs/Talkse-FlowCRM-10-Phase-Plan.pdf`) is in a public repo | Remove or make the repo private if needed |
| L8 | Repo | `flowcrm/.github` workflows don't run from a subfolder | Move to the repo root `.github/` if CI is wanted |
| L9 | Code | Legacy names from the medspa origin (`clinic_config.py`, `clinic_data.json`, `CLINIC`) | Rename to `business_config` after the event |
| L10 | UX | Call page not verified on real phones (mic permissions on iOS Safari, orb performance) | Test on one iPhone and one Android before the demo |

## Recommended order (before 12:15 PM)

1. **C1**: fix the FlowCRM `PORT` variable (1 min).
2. **C3 + C2**: remove `owner_approved` from MCP `book_visit`; add an `MCP_API_KEY` header check (about 20 min).
3. **H1**: set `OWNER_CONSOLE_KEY` on the Talkse service (2 min).
4. **H2**: ask for the caller's phone number (about 15 min).
5. Commit, merge into `flowcrm-deploy`, push, rebuild both services, live test.
6. After the event: **H3** (rotate all keys), then H5–H7 and the Medium items.
