"""Talkse web server: browser calls over HTTP, deployed on Alternate Clouds.

Endpoints are plain `def` (not async) on purpose: the Groq, Deepgram and psycopg2
calls are blocking, so FastAPI runs them in its thread pool.
"""

import base64
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # must run before clinic_config reads DATA_FILE

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import call_engine
import clinic_config
import db
import handoff
import mcp_server
import notifier
import telegram_bot
from poc import synthesize_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("talkse")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # ~5 MB is several minutes of webm speech

# Fixed phrases are synthesized once and reused
_tts_cache: dict[str, str] = {}
CACHED_PHRASES = {call_engine.GREETING}


# MCP server (Streamable HTTP) mounted at /mcp; its session manager runs inside our lifespan
mcp_app = mcp_server.mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not db.DATABASE_URL.startswith(("postgres://", "postgresql://")):
        log.error("DATABASE_URL must be the Neon connection string (postgresql://...), got a value starting with %r",
                  db.DATABASE_URL[:12])
    try:
        db.init_db()
    except Exception as e:
        # Keep serving /health and the page so the Logs tab shows the real problem
        log.error("Database init failed: %s", e)
    telegram_stop = telegram_bot.start_polling()
    if telegram_bot.token() and not telegram_bot.owner_chat():
        log.warning("TELEGRAM_OWNER_CHAT_ID not set: send /start to the bot to get your chat ID")
    async with mcp_server.mcp.session_manager.run():
        yield
    if telegram_stop:
        telegram_stop.set()


app = FastAPI(title="Talkse", lifespan=lifespan)
app.mount("/mcp", mcp_app)


def tts_b64(text: str) -> str | None:
    """Returns base64 MP3 for the text, or None if TTS fails (the page then shows text only)."""
    if text in _tts_cache:
        return _tts_cache[text]
    audio = None
    for attempt in (1, 2):
        try:
            audio = base64.b64encode(synthesize_bytes(text)).decode("ascii")
            break
        except Exception as e:
            log.error("TTS attempt %d failed: %s", attempt, e)
    if audio is None:
        return None
    if text in CACHED_PHRASES:
        _tts_cache[text] = audio
    return audio


class StartCallRequest(BaseModel):
    caller_phone: str | None = None


class OwnerAnswer(BaseModel):
    choice: str | None = None       # option id: approve / site_visit / callback
    text: str | None = None         # typed answer to a caller's question
    save_as_faq: bool = False       # teach the AI this answer for next time


def require_owner(key: str | None) -> None:
    """Optional protection for the owner console: set OWNER_CONSOLE_KEY to require ?key=... / X-Owner-Key."""
    expected = os.getenv("OWNER_CONSOLE_KEY")
    if expected and key != expected:
        raise HTTPException(status_code=401, detail="Owner key required")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "call.html"))


@app.get("/api/info")
def info() -> dict:
    """Business details for the call page: name, owner, today's hours, services, service area."""
    clinic = clinic_config.CLINIC
    now = clinic_config.now()
    today = clinic.get("hours", {}).get(now.strftime("%A").lower(), {})
    address = clinic.get("address", {})
    return {
        "name": clinic.get("name", "Talkse"),
        "tagline": clinic.get("tagline", ""),
        "owner": clinic_config.OWNER_NAME,
        "owner_title": clinic.get("owner", {}).get("title", "Owner"),
        "phone": clinic.get("phone"),
        "emergency_line": clinic.get("after_hours_emergency_line"),
        "city": ", ".join(p for p in (address.get("city"), address.get("state")) if p),
        "timezone": clinic.get("timezone"),
        "local_time": now.strftime("%a %I:%M %p").replace(" 0", " "),
        "open_now": clinic_config.is_clinic_open(now),
        "hours_today": "Closed today" if today.get("closed") or not today.get("open")
        else f"{today['open']}–{today['close']}",
        "service_area": clinic.get("service_area", []),
        "payment_methods": clinic.get("accepted_payment_methods", []),
        "services": [
            {"name": s["name"], "price_usd": s.get("price_usd"), "description": s.get("description", ""),
             "needs_approval": bool(s.get("approval_required"))}
            for s in clinic_config.SERVICES
        ],
        "technicians": [{"name": p["name"], "title": p.get("title", "")} for p in clinic_config.PROVIDERS],
        "crm_url": os.getenv("FLOWCRM_URL") or None,
    }


@app.post("/api/calls")
def start_call(body: StartCallRequest | None = None) -> dict:
    call_id, greeting = call_engine.start_call(body.caller_phone if body else None)
    return {"call_id": call_id, "reply_text": greeting, "audio_b64": tts_b64(greeting), "status": call_engine.COLLECTING}


@app.post("/api/calls/{call_id}/turn")
def call_turn(call_id: str, audio: UploadFile = File(...)) -> dict:
    data = audio.file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Recording is too long")
    if not data:
        raise HTTPException(status_code=400, detail="Empty recording")

    try:
        result = call_engine.handle_turn(call_id, data, audio.filename or "turn.webm")
    except KeyError:
        raise HTTPException(status_code=404, detail="Call not found")

    result["audio_b64"] = tts_b64(result["reply_text"])
    return result


@app.get("/api/calls/{call_id}/status")
def call_status(call_id: str) -> dict:
    """Polled every 2s while the caller waits for the owner. Carries the owner's answer as speech once it arrives."""
    status = call_engine.get_status(call_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Call not found")
    if status.get("new_reply"):
        status["audio_b64"] = tts_b64(status["reply_text"])
    return status


# ---- Owner console (human in the loop) ---------------------------------------

@app.get("/owner")
def owner_page() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "owner.html"))


@app.get("/api/owner/escalations")
def owner_escalations(key: str | None = None, x_owner_key: str | None = Header(default=None)) -> dict:
    require_owner(key or x_owner_key)
    rows = db.list_escalations()
    return {
        "owner": clinic_config.OWNER_NAME,
        "business": clinic_config.BUSINESS_NAME,
        "timeout_seconds": int(os.getenv("HANDOFF_TIMEOUT_SECONDS", "45")),
        "channels": notifier.active_channels(),
        "escalations": [
            {
                "id": str(r["id"]),
                "call_id": str(r["call_id"]) if r["call_id"] else None,
                "kind": r["kind"],
                "status": r["status"],
                "summary": r["summary"],
                "options": (r["options_json"] or {}).get("options", []) if isinstance(r["options_json"], dict) else [],
                "answer": r["answer"],
                "caller_name": r.get("caller_name"),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }


@app.post("/api/owner/escalations/{escalation_id}/answer")
def owner_answer(escalation_id: str, body: OwnerAnswer, key: str | None = None,
                 x_owner_key: str | None = Header(default=None)) -> dict:
    require_owner(key or x_owner_key)
    result = handoff.answer_escalation(escalation_id, choice=body.choice, text=body.text, save_as_faq=body.save_as_faq)
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@app.post("/api/owner/escalations/{escalation_id}/ack")
def owner_ack(escalation_id: str, key: str | None = None, x_owner_key: str | None = Header(default=None)) -> dict:
    require_owner(key or x_owner_key)
    return handoff.acknowledge(escalation_id)


@app.get("/api/owner/faqs")
def owner_faqs(key: str | None = None, x_owner_key: str | None = Header(default=None)) -> dict:
    """FAQs the owner taught the AI (Save as FAQ)."""
    require_owner(key or x_owner_key)
    return {"faqs": [{"question": f["question"], "answer": f["answer"], "source": f["source"]} for f in db.get_faqs()]}


# ---- Reserved for channel webhooks (Telegram, SMS...) -------------------------

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request,
                           x_telegram_bot_api_secret_token: str | None = Header(default=None)) -> dict:
    """Used only with TELEGRAM_MODE=webhook (default is polling, which needs no webhook).
    Register it once: setWebhook url=<PUBLIC_BASE_URL>/telegram/webhook secret_token=<TELEGRAM_WEBHOOK_SECRET>."""
    if telegram_bot.mode() != "webhook":
        raise HTTPException(status_code=409, detail="Telegram is in polling mode")
    expected = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if not expected or x_telegram_bot_api_secret_token != expected:
        raise HTTPException(status_code=401, detail="Bad secret token")
    update = await request.json()
    import asyncio
    await asyncio.to_thread(telegram_bot.handle_update, update)  # blocking DB + Telegram calls
    return {"ok": True}
