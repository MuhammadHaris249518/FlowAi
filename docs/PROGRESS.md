# Talkse Live + FlowCRM: progress report

**As of:** 17 October 2026, early morning PKT · **Deadline:** 12:45 PM PKT today (target 12:15 PM)

## Status: about 75% complete

All features are built and tested locally. What remains is deploying the latest version, testing it live, and the submission work (video, screenshots, LinkedIn post, PDF).

| Area | Weight | Done | Contributes |
|---|---|---|---|
| Build: phases 1 to 8 | 70% | 100% | 70% |
| Phase 9: final deploy and live testing | 15% | ~40% | 6% |
| Phase 10: video, screenshots, LinkedIn, submission PDF | 15% | 0% | 0% |
| **Total** | | | **~76%** |

## Phases

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Setup and first deploy | Done | Deploy pipeline on Alternate Clouds verified, WebSockets work |
| 2 | Database | Done | One Neon Postgres: FlowCRM in `public`, Talkse in `talkse`; demo data seeded (`seed_crm.py --reset`) |
| 3 | Business data | Done | Cool Breeze Home Services, Austin TX (US); owner Haris; 9 services in USD, 4 technicians, service area, 15 FAQs, safety rules |
| 4 | Browser voice call | Done | Hold-to-talk call page; one turn per request, state saved in Postgres |
| 5 | FlowCRM demo mode | Done | Same FlowCRM UI without login, 5-second live refresh, single-container deploy, Voice Agent button |
| 6 | Calls into FlowCRM | Done | Every call creates a contact, lead, deal (Meeting column), task and activity |
| 7 | Human handoff | Done | Caller waits while the owner decides; owner console and Telegram bot with buttons; 45 s timeout falls back to a callback |
| 8 | Safety and learning | Done | Gas/sparks/smoke alerts the owner instantly; owner answers can be saved as FAQs the AI uses next time |
| 9 | Final deploy and testing | In progress | Everything tested locally; latest version not yet deployed |
| 10 | Demo and submission | Not started | Video, screenshots, LinkedIn post, submission PDF |

## What works (tested end to end on the real database)

| Flow | Result |
|---|---|
| Normal booking ("my AC is blowing warm air") | Booked with a technician at the requested time; deal appears in the FlowCRM pipeline |
| Owner approval (new AC install, commercial job, over $1,500, outside service area) | Caller on hold; owner taps Approve / Free estimate first / I'll call; AI continues the call |
| Unknown question | AI asks the owner instead of guessing and speaks the owner's answer |
| Learning | Owner taps Save as FAQ; the next caller gets the answer instantly, no owner needed |
| Safety hazard ("I smell gas") | Immediate safety instructions, owner alert, HIGH task in FlowCRM |
| Owner doesn't answer | Callback promised after 45 s, HIGH callback task in FlowCRM |
| Problem described without "book" ("my sink is leaking") | Treated as a booking, service worked out from the problem |
| Telegram bot | All flows pass in simulation (buttons, typed replies, Save as FAQ, alerts, strangers blocked) |

## What was built at the event

- **Talkse service** (Python, FastAPI): browser calls, conversation engine, human handoff, owner console, Telegram bot, FAQ learning, CRM sync, US business data, new call page with an animated voice orb.
- **FlowCRM** (Next.js + Express): demo mode without login, live refresh, Voice Agent button and sidebar links, single-container Dockerfile.
- **Deployment:** monorepo (`talkse/`, `flowcrm/`), `flowcrm-deploy` branch for the FlowCRM service, local launcher (`scripts/run_local.py`).

Pre-existing code reused: the Talkse voice pipeline (speech-to-text, intent extraction, booking engine) and the FlowCRM app. Both are listed in the README.

## Remaining work

| # | Task | Owner | Time |
|---|---|---|---|
| 1 | Push `main` and `flowcrm-deploy` | Team | 2 min |
| 2 | Talkse service: set variables, Rebuild, Deploy | Team | 10 min |
| 3 | FlowCRM service: set `PORT=80`, `DEMO_CLERK_ID=demo_haris`, Rebuild, Deploy | Team | 15 min |
| 4 | Get the owner's Telegram chat ID (send /start to the bot after deploy) and set it | Team | 5 min |
| 5 | Live test on a phone: booking, approval, Save as FAQ, safety call | Team + Claude | 20 min |
| 6 | Record the 2 to 3 minute demo video | Team | 30 min |
| 7 | Screenshots: both teammates in Discord, Alternate Clouds dashboard with both services | Team | 10 min |
| 8 | LinkedIn post (must be written by the team, not AI) and teammate reshare | Team | 20 min |
| 9 | Submission PDF (sections 1 to 5 of the handbook) | Team | 30 min |

**Estimated remaining time:** about 2.5 hours, plus buffer for deploy retries.

## Deployment checklist

**Talkse service** (branch `main`, root directory `.`, port 8000). Variables:
`DATABASE_URL`, `GROQ_API_KEY`, `DEEPGRAM_API_KEY`, `CRM_ORG_ID=org_coolbreeze`, `CRM_OWNER_USER_ID=usr_haris`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_CHAT_ID`, `PUBLIC_BASE_URL` (its own URL), `FLOWCRM_URL` (the FlowCRM URL).

**FlowCRM service** (branch `flowcrm-deploy`, port 80). Variables:
`DATABASE_URL` (without `channel_binding`), `DIRECT_URL`, `AUTH_MODE=demo`, `DEMO_CLERK_ID=demo_haris`, `DEMO_ORG_ID=org_coolbreeze`, `PORT=80`.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Alternate Clouds / Akash deploy rate limits (`429`) | Deploys fail or are delayed | Wait 2 to 3 minutes between attempts; deploy early |
| Deepgram key rejected intermittently | Robotic browser voice instead of Deepgram | Browser voice fallback is built in; check the key on the deployed server |
| University network blocks Telegram | Telegram can't be tested from laptops on campus Wi-Fi | Deployed server reaches Telegram; test with phones on mobile data |
| Shared keys exposed in chat (database password, bot token, API keys) | Misuse after the event | Rotate all keys after the closing ceremony |
| Per-turn latency over 4 s on slow networks | Slower conversation | Measure on the deployed server; add connection pooling if needed |
