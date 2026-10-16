# Talkse Live + FlowCRM

An AI voice receptionist for home-services businesses that asks the owner instead of guessing,
plus the CRM where every call, job and decision is recorded.

Built for Agentika 2.0 (NaSCon 2026), theme "Human + AI: Better Together".

## Repository layout

```
.
├── talkse/        AI voice receptionist (Python, FastAPI)
│   ├── server.py          HTTP API + call page
│   ├── call_engine.py     one conversation turn at a time, state in Postgres
│   ├── crm_sync.py        writes finished calls into FlowCRM's tables
│   ├── seed_crm.py        demo data for FlowCRM
│   ├── poc.py             speech-to-text, intent extraction, text-to-speech
│   ├── booking_engine.py  slots, booking, reschedule, cancel
│   ├── clinic_config.py   business data lookup (DATA_FILE)
│   ├── db.py              Postgres access (schema "talkse")
│   └── static/call.html   browser call page ("hold to talk")
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
  CRM integration (`crm_sync.py`, `seed_crm.py`), FlowCRM demo mode (no login, live refresh,
  single-container deploy), and the human handoff flow.

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
