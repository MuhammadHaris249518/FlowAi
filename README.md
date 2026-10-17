# Talkse Live + FlowCRM

An AI voice receptionist for a US home-services company (AC, plumbing, electrical) that asks the
owner mid-call instead of guessing, plus the CRM where every call, job and decision is recorded.
Demo company: Cool Breeze Home Services, Austin TX. Owner: Haris.

Built for Agentika 2.0 (NaSCon 2026), theme "Human + AI: Better Together".

## Repository layout

```
.
├── talkse/        AI voice receptionist (Python, FastAPI)
│   ├── server.py          HTTP API, call page, owner console API
│   ├── call_engine.py     one conversation turn at a time, state in Postgres
│   ├── handoff.py         human in the loop: owner approvals, live answers, timeouts, hazards
│   ├── notifier.py        pluggable owner notifications (console, Telegram; SMS slot)
│   ├── telegram_bot.py    owner's phone: handoff buttons, typed answers, Save as FAQ, safety alerts
│   ├── faq.py             answers questions from FAQs + staff-saved answers (learning)
│   ├── crm_sync.py        writes finished calls into FlowCRM's tables
│   ├── seed_crm.py        demo data for FlowCRM
│   ├── poc.py             speech-to-text, intent extraction, text-to-speech
│   ├── booking_engine.py  slots, booking, reschedule, cancel
│   ├── clinic_config.py   business data lookup (DATA_FILE, default company_data.json)
│   ├── company_data.json  demo business: services, technicians, prices, service area, FAQs, safety rules
│   ├── db.py              Postgres access (schema "talkse")
│   ├── static/call.html   browser call page ("hold to talk")
│   └── static/owner.html  owner console (/owner): approve, answer, Save as FAQ, safety alerts
├── flowcrm/       CRM (Next.js web + Express/Prisma API), demo mode without login
│   ├── apps/api           Express API (Prisma, schema "public")
│   ├── apps/web           Next.js UI
│   ├── Dockerfile         one container: web on 3000, API on 4000 behind it
│   └── start.sh
├── docs/          team plan
├── Dockerfile     builds talkse/ (deployed service)
└── .env.example   all environment variables
```

Both apps share one Neon Postgres database: FlowCRM owns the `public` schema, Talkse owns `talkse`.

## What existed before the event, and what was built here

- **Before:** the Talkse voice pipeline (`poc.py`, `booking_engine.py`, `clinic_config.py`, `db.py`,
  from github.com/sheix-khizar/Talkse) and FlowCRM (github.com/MuhammadHaris249518/FlowCRM).
- **Built at Agentika 2.0:** browser calls (`server.py`, `call_engine.py`, `static/call.html`),
  the human-in-the-loop handoff, owner console and Telegram bot (`handoff.py`, `notifier.py`,
  `telegram_bot.py`, `static/owner.html`),
  safety alerts and the learning FAQ loop (`faq.py`), the US home-services business data,
  CRM integration (`crm_sync.py`, `seed_crm.py`) and FlowCRM demo mode (no login, live refresh,
  single-container deploy).

## Where the human comes in

| Situation | What the AI does | What the owner does |
|---|---|---|
| Install, commercial job, big estimate, outside service area | Puts the caller on hold: "let me check with Haris" | Approves, offers a free estimate visit first, or chooses to call back |
| Question no FAQ answers | Asks the owner live instead of guessing | Types the answer; the AI speaks it. "Save as FAQ" teaches the AI for next time |
| Gas smell, sparks, smoke, CO alarm | Gives safety instructions immediately, never waits | Gets an urgent alert + HIGH task in FlowCRM |
| Owner doesn't answer in 45 s | Promises a callback | Gets a HIGH callback task in FlowCRM |

## Run locally

Talkse (Python 3.11+):

```
python -m venv .venv
.venv\Scripts\pip install -r talkse\requirements.txt
cd talkse
..\.venv\Scripts\python -m uvicorn server:app --port 8000
```

FlowCRM (Node 20+), from `flowcrm/`:

```
npm ci
cd apps/api && npx prisma db push --schema=src/prisma/schema.prisma && cd ../..
python ..\talkse\seed_crm.py
```

Then run the API (`AUTH_MODE=demo`, `DEMO_CLERK_ID=demo_ahmed`, `DEMO_ORG_ID=org_coolbreeze`) with
`npm run dev:api` and the web app (`NEXT_PUBLIC_AUTH_MODE=demo`) with `npm run dev:web`.

Environment variables are listed in `.env.example`. Never commit `.env`.
