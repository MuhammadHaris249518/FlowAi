# Talkse Live + FlowCRM: progress report

**As of:** 17 October 2026, afternoon PKT · **Deadline:** 12:45 PM PKT today

## Status: about 85% complete

Everything is built, tested locally against the real database, and pushed. What remains depends on the
platform: Alternate Clouds paused deployments for a fix ("stop deployments until I ping you"). After their
ping: redeploy both services, point SignalWire at the new code, run the live test, then the submission work.

| Area | Weight | Done | Contributes |
|---|---|---|---|
| Build: phases 1 to 8, plus phone calls, email alerts, automation, security fixes | 70% | 100% | 70% |
| Phase 9: final deploy and live testing | 15% | ~60% (code ready, variables ready, platform paused) | 9% |
| Phase 10: video, screenshots, LinkedIn, submission PDF | 15% | ~40% (video can be recorded locally) | 6% |
| **Total** | | | **~85%** |

## Phases

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Setup and first deploy | Done | Alternate Clouds pipeline verified |
| 2 | Database | Done | One Neon Postgres: FlowCRM in `public`, Talkse in `talkse`; pooled connections |
| 3 | Business data | Done | Cool Breeze Home Services, Austin TX; owner Haris; 9 services, 4 technicians, 15 FAQs, safety rules |
| 4 | Voice calls | Done | Browser call page (new light design, voice orb) **and real phone calls** on +1 201 586 7069 (SignalWire) |
| 5 | FlowCRM demo mode | Done | Same FlowCRM UI without login; Voice Agent button; Documents hidden |
| 6 | Calls into FlowCRM | Done | Contact (with phone), lead, deal, task, activity, plus automation events and Inbox notifications |
| 7 | Human handoff | Done | Caller on hold while Haris decides; **approval email** with signed one-tap links, owner console; 90 s timeout falls back to a callback |
| 8 | Safety and learning | Done | Gas/sparks/smoke alerts (no false alarm for a beeping detector); owner answers saved as FAQs |
| 9 | Final deploy and testing | Waiting on platform | Both deployments paused by the platform team; variable sheets in `deploy/*.cloud.env` |
| 10 | Demo and submission | In progress | Record the video locally now; screenshots, LinkedIn post (team-written), PDF |

## What works (tested on the real database)

| Flow | Result |
|---|---|
| Booking on the web ("my sink is leaking") | Asks city, time, name, **phone**, reads the booking back, books on "yes" |
| Booking by phone (SignalWire) | Same flow; phone taken from caller ID; ~2.5 s per turn; slow turns never drop the call |
| Changing details at the read-back ("make it 4 PM") | Keeps the day, re-reads, books the corrected time |
| Unclear time ("sometime soon") | Asked again instead of guessed |
| Owner approval (install, commercial, outside area) | Caller on hold; Haris gets an email with Approve / Site visit / Call back; caller hears the decision |
| Unknown question | Haris answers in the owner console; can save it as an FAQ |
| Safety hazard | Instant safety steps, urgent email, HIGH task |
| Caller leaves mid-call (web or phone) | Within ~10 min: call-back task, CRM lead and Inbox notification |
| FlowCRM automation | 3 live workflows: hot lead → task + alert; booking → AI confirmation email draft; big job → owner alert; overdue tasks trigger too |
| FlowCRM Inbox | Real notifications from the AI receptionist and automation, plus AI drafts |
| FlowCRM AI Assistant over MCP | Reads calls, checks availability, books routine jobs; **cannot approve jobs** (only Haris can) |
| Security | `/mcp` needs a key; owner console key in production; approvals can't be bypassed; per-IP rate limits |
| Unit tests | `python -m pytest talkse/tests` (17 tests) |

## Remaining work

| # | Task | Owner | Time |
|---|---|---|---|
| 1 | Wait for the platform team's ping | Team | — |
| 2 | Talkse: paste `deploy/talkse.cloud.env` into Variables, Ports 8000, Redeploy | Team | 5 min |
| 3 | FlowCRM: paste `deploy/flowcrm.cloud.env` into Variables, Ports 80, Redeploy | Team | 5 min |
| 4 | SignalWire: handler `talkse-webhook-cxml` → `<Talkse URL>/voice/incoming` (POST) | Team | 2 min |
| 5 | Live test: web call, phone call, email approval from a phone, FlowCRM Inbox/automation | Team + Claude | 15 min |
| 6 | Demo video (can be recorded locally now) | Team | 30 min |
| 7 | Screenshots, LinkedIn post (written by the team), submission PDF | Team | 45 min |

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Platform outage continues past the deadline | No live URL for judges | Record the full demo locally; keep the variable sheets ready to deploy in minutes |
| SignalWire trial limits | Calls only from verified numbers | Verify the presenter's phone; Click-to-Test in the SignalWire dashboard |
| Resend free tier | Emails only to the account owner's address | Fine for owner alerts; verify a domain later for customer emails |
| Keys exposed in chat (Neon, Groq, Deepgram, Telegram, Resend) | Misuse after the event | Rotate all of them after the closing ceremony |
