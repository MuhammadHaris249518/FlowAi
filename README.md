# Talkse Live + Cool Breeze CRM

An AI voice receptionist for a US home-services company (AC, plumbing, electrical) that books routine
jobs by itself and **asks the owner mid-call** for the decisions that matter, plus the CRM where every
call, job and decision is recorded.

Demo company: **Cool Breeze Home Services**, Austin TX. Owner: **Haris**. Phone line: **+1 201 586 7069**.
Built for Agentika 2.0 (NaSCon 2026), theme "Human + AI: Better Together". Deployed on Alternate Clouds.

## Who it's for

Small home-service businesses that miss calls while the owner is on a job. The AI answers 24/7 and
books routine work; installs, commercial jobs, unknown questions and emergencies go to the owner.

## Where the human comes in

| Situation | What the AI does | What the owner does |
|---|---|---|
| Install, commercial job, big estimate, outside the service area | Puts the caller on hold: "let me check with Haris" | Gets an **email** with one-tap buttons (Approve / Book a site visit / I'll call back), or uses the owner console |
| Question no FAQ answers | Asks the owner live instead of guessing | Types the answer in the owner console; the AI speaks it. "Save as FAQ" teaches the AI for next time |
| Gas smell, sparks, smoke, CO alarm | Gives safety instructions immediately, never waits | Gets an urgent email + HIGH task in the CRM |
| Owner doesn't answer in time (90 s) | Promises a call back | Gets a HIGH call-back task |
| Caller hangs up or closes the page mid-call | — | Within ~10 min: call-back task, CRM lead and Inbox notification |
| CRM AI Assistant tries to approve a job | Refused | Only the owner approves |

Approval links are signed (HMAC) and open a confirm page first, so a mail scanner or a guessed URL
can't approve anything.

## What it does

**Voice receptionist (Talkse)**
- Browser calls (`/`, hold-to-talk, animated voice orb) and **real phone calls** through SignalWire.
- Understands the problem ("my sink is leaking"), asks only for what's missing (city, time, name,
  phone), **reads the booking back** and books on "yes"; changes keep what wasn't changed.
- Books a real technician at a free slot, reschedules and cancels; unclear times are asked again.
- Owner alerts by email (Resend) and the owner console (`/owner`).
- MCP server (`/mcp`, key-protected) exposing the receptionist's tools to other AI clients.

**CRM (Cool Breeze, built on FlowCRM)**
- Every call becomes a contact (with phone), lead, deal, task and activity, live on the dashboard.
- **Inbox**: notifications from the AI receptionist and automation, plus AI-drafted emails to review.
- **Automation**: workflows triggered by calls, bookings, pipeline moves and overdue tasks; actions
  create tasks, update leads, notify the owner, or draft an email with AI. Three workflows ship active.
- **AI Assistant** that uses the receptionist's tools over MCP (calls, availability, routine bookings).

## How it's built

All custom code, no agent framework.

| Part | Technology |
|---|---|
| Voice agent | Python, FastAPI; our own turn-by-turn pipeline with state in Postgres |
| Speech to text | Groq Whisper (`whisper-large-v3-turbo`) |
| Understanding | Groq `openai/gpt-oss-20b` (intent + details as JSON), rules for booking/approval/safety |
| Voice | Deepgram Aura (browser speech as fallback); SignalWire `<Say>` on phone calls |
| Phone calls | SignalWire cXML webhooks (`/voice/*`) |
| Owner alerts | Resend email; owner console |
| CRM | Next.js 14 + Express + Prisma (FlowCRM), demo mode without login |
| Agent-to-CRM link | Model Context Protocol: Talkse is an MCP server, the CRM assistant an MCP client |
| Database | One Neon Postgres: CRM in `public`, Talkse in `talkse` |
| Hosting | Alternate Clouds (two services) |

## Repository layout

```
.
├── talkse/                AI voice receptionist (Python, FastAPI)
│   ├── server.py              HTTP API, call page, owner console API, /owner/act email links, /mcp, rate limits, call sweeper
│   ├── call_engine.py         one conversation turn at a time: details, phone, read-back, booking
│   ├── telephony.py           real phone calls (SignalWire cXML)
│   ├── handoff.py             human in the loop: approvals, live answers, timeouts, hazards
│   ├── notifier.py            pluggable owner notifications (console, email; Telegram/SMS optional)
│   ├── email_notify.py        owner emails via Resend, signed approval links
│   ├── mcp_server.py          MCP tools (book_visit needs the owner's approval for approval jobs)
│   ├── faq.py                 FAQ answers + owner-saved answers (learning)
│   ├── crm_sync.py            writes finished calls into the CRM + automation events + Inbox notifications
│   ├── booking_engine.py      slots, booking, reschedule, cancel, safety and approval rules
│   ├── db.py                  Postgres access (schema "talkse", connection pool)
│   ├── seed_crm.py            demo CRM data + 3 automation workflows
│   ├── company_data.json      demo business: services, technicians, prices, area, FAQs, safety rules
│   ├── telegram_bot.py        optional Telegram channel (off in production)
│   ├── tests/                 unit tests (pytest)
│   └── static/                call.html (call page), owner.html (owner console)
├── flowcrm/               CRM (Next.js web + Express/Prisma API), shown as "Cool Breeze"
├── deploy/                Dockerfile for the CRM service, deploy notes, *.cloud.env (gitignored)
├── scripts/run_local.py   runs everything locally from .env
├── docs/                  PROGRESS.md, AUDIT.md
├── Dockerfile             builds talkse/ (Talkse service)
└── .env.example           all environment variables
```

## What existed before the event, and what was built here

- **Before:** the Talkse voice pipeline core (`poc.py`, `booking_engine.py`, `clinic_config.py`, `db.py`,
  from github.com/sheix-khizar/Talkse) and the FlowCRM app (github.com/MuhammadHaris249518/FlowCRM).
- **Built at Agentika 2.0:** browser and phone calls (`server.py`, `call_engine.py`, `telephony.py`,
  `static/call.html`), the human-in-the-loop handoff with owner console and email approvals
  (`handoff.py`, `notifier.py`, `email_notify.py`, `static/owner.html`), safety alerts and the FAQ
  learning loop, the US business data, the MCP server and CRM assistant integration, CRM sync with
  automation events and Inbox notifications, working automation (notify-owner and AI email actions,
  pipeline and overdue triggers), the real Inbox, CRM demo mode and rebrand, security fixes, tests.

## Run locally

Needs Python 3.11+, Node 20+, and a filled-in `.env` (copy `.env.example`).

```
python -m venv .venv
.venv\Scripts\pip install -r talkse\requirements.txt
cd flowcrm && npm ci && cd apps/api && npx prisma db push --schema=src/prisma/schema.prisma && cd ../../..
.venv\Scripts\python talkse\seed_crm.py
```

Then, each in its own terminal:

```
.venv\Scripts\python scripts\run_local.py talkse   # http://localhost:8000  call page, /owner
.venv\Scripts\python scripts\run_local.py api      # http://localhost:4000  CRM API
.venv\Scripts\python scripts\run_local.py web      # http://localhost:3000  CRM UI
```

Phone calls and the buttons in approval emails need the public URL (`PUBLIC_BASE_URL`); locally,
approve from `localhost:8000/owner`.

Tests: `.venv\Scripts\python -m pytest talkse/tests -q`

## Deploy

Two Alternate Clouds services; see [deploy/README.md](deploy/README.md).

## Security notes

- `/mcp` requires `MCP_API_KEY` (the CRM sends it as `TALKSE_MCP_KEY`).
- The owner console requires `OWNER_CONSOLE_KEY` in production (`/owner?key=...`).
- Per-IP rate limits on new calls and turns.
- CRM demo mode has no login: use it only with demo data.
- Never commit `.env` or `deploy/*.cloud.env`.
