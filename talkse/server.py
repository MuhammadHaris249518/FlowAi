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

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import call_engine
import clinic_config
import db
from poc import synthesize_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("talkse")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # ~5 MB is several minutes of webm speech

# Fixed phrases are synthesized once and reused
_tts_cache: dict[str, str] = {}
CACHED_PHRASES = {call_engine.GREETING}


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
    yield


app = FastAPI(title="Talkse", lifespan=lifespan)


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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "call.html"))


@app.get("/api/info")
def info() -> dict:
    """Business name and phone for the call page header."""
    return {
        "name": clinic_config.CLINIC.get("name", "Talkse"),
        "tagline": clinic_config.CLINIC.get("tagline", ""),
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
    status = call_engine.get_status(call_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return status
