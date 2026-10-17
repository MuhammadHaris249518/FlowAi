"""Real incoming phone calls via SignalWire cXML (Twilio-compatible XML webhooks).

SignalWire setup (Phone Numbers -> your number -> Voice):
    "When a call comes in":  cXML webhook  POST  <PUBLIC_BASE_URL>/voice/incoming
    "Status callback":                      POST  <PUBLIC_BASE_URL>/voice/status   (optional, recommended)
Add ?key=<VOICE_WEBHOOK_KEY> to both URLs if VOICE_WEBHOOK_KEY is set.

Flow per call:
    /voice/incoming  -> new call (caller's number from `From`), greeting inside <Gather input="speech">
    /voice/turn      -> SignalWire's transcript (SpeechResult) -> call_engine.handle_text_turn -> next <Gather>
    /voice/wait      -> while the owner decides: hold, re-check every few seconds, then speak the answer
    /voice/status    -> caller hung up mid-call: close the call so the owner gets a callback task in FlowCRM

Everything after the transcript is the same engine as browser calls: booking, owner handoff,
Telegram, safety alerts, FAQ learning and CRM sync.
"""

import os
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, Form, HTTPException, Query
from fastapi.responses import Response

import call_engine
import clinic_config as config
import crm_sync
import db

router = APIRouter(prefix="/voice")

MAX_SILENT_TURNS = 2
HOLD_SECONDS = 4
# SignalWire drops a webhook after ~15 s, and one AI turn (LLM + DB) can take longer.
# Each turn runs in a worker; if it isn't done within INLINE_WAIT_SECONDS the caller hears
# "One moment" and SignalWire is redirected to /voice/reply, which picks up the result.
INLINE_WAIT_SECONDS = 8
_workers = ThreadPoolExecutor(max_workers=8, thread_name_prefix="voice-turn")
_pending: dict[str, Future] = {}  # call_id -> turn still being processed (single instance)

# Speech-recognition hints: services and cities the caller is likely to say
HINTS = ", ".join(
    [s["name"] for s in config.SERVICES] + list(config.CLINIC.get("service_area", []))
    + ["AC", "air conditioner", "furnace", "water heater", "leak", "plumbing", "electrical", config.OWNER_NAME]
)


def _check_key(key: str | None) -> None:
    expected = os.getenv("VOICE_WEBHOOK_KEY")
    if expected and key != expected:
        raise HTTPException(status_code=401, detail="Bad webhook key")


def _url(path: str, **params) -> str:
    """Absolute webhook URL (SignalWire follows it for the next step), keeping the webhook key."""
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if os.getenv("VOICE_WEBHOOK_KEY"):
        params["key"] = os.getenv("VOICE_WEBHOOK_KEY")
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    return f"{base}/voice/{path}" + (f"?{query}" if query else "")


def _say(text: str) -> str:
    voice = os.getenv("VOICE_TTS_VOICE", "woman")
    return f"<Say voice={quoteattr(voice)} language=\"en-US\">{escape(text)}</Say>"


def _xml(body: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>',
                    media_type="application/xml")


def _gather(call_id: str, text: str, silent: int = 0) -> Response:
    """Speak, then listen for the caller's next sentence; silence falls through to the redirect."""
    action = escape(_url("turn", call_id=call_id))
    fallback = escape(_url("turn", call_id=call_id, silent=silent + 1))
    return _xml(
        f'<Gather input="speech" action="{action}" method="POST" speechTimeout="auto" '
        f'timeout="7" language="en-US" hints={quoteattr(HINTS)}>{_say(text)}</Gather>'
        f'<Redirect method="POST">{fallback}</Redirect>'
    )


def _respond(call_id: str, reply_text: str, status: str, silent: int = 0) -> Response:
    if status == call_engine.COLLECTING:
        return _gather(call_id, reply_text, silent)
    if status == call_engine.WAITING_HUMAN:
        wait = escape(_url("wait", call_id=call_id))
        return _xml(f'{_say(reply_text)}<Pause length="{HOLD_SECONDS}"/><Redirect method="POST">{wait}</Redirect>')
    # done / needs_human / emergency: final words, then hang up
    return _xml(f"{_say(reply_text)}<Say>Goodbye.</Say><Hangup/>")


@router.post("/incoming")
def incoming(CallSid: str = Form(""), From: str = Form(""), key: str | None = Query(None)) -> Response:
    _check_key(key)
    call_id, greeting = call_engine.start_call(caller_phone=From or None)
    call = db.get_call(call_id)
    state = call["state_json"]
    state["channel"] = "phone"
    state["call_sid"] = CallSid
    db.update_call(call_id, state)
    return _gather(call_id, greeting)


@router.post("/turn")
def turn(call_id: str = Query(...), silent: int = Query(0), key: str | None = Query(None),
         SpeechResult: str = Form("")) -> Response:
    _check_key(key)
    if not SpeechResult.strip():
        if silent >= MAX_SILENT_TURNS:
            return _xml(f"{_say('I can not hear you, so I will end the call. Please call us back anytime.')}<Hangup/>")
        return _gather(call_id, "Sorry, I didn't catch that. Could you say it again?", silent)
    if call_id in _pending:  # SignalWire retried while the previous turn is still running
        return _await_turn(call_id, filler="")
    _pending[call_id] = _workers.submit(call_engine.handle_text_turn, call_id, SpeechResult)
    return _await_turn(call_id, filler="One moment.")


@router.post("/reply")
def reply(call_id: str = Query(...), key: str | None = Query(None)) -> Response:
    """Follow-up for a slow turn: wait for the worker's result, then continue the conversation."""
    _check_key(key)
    if call_id not in _pending:
        return _gather(call_id, "Sorry, could you say that again?")
    return _await_turn(call_id, filler="")


def _await_turn(call_id: str, filler: str) -> Response:
    """Answer inline if the turn finishes in time, otherwise keep the line alive and come back."""
    future = _pending[call_id]
    try:
        result = future.result(timeout=INLINE_WAIT_SECONDS)
    except FutureTimeout:
        again = escape(_url("reply", call_id=call_id))
        return _xml(f'{_say(filler) if filler else ""}<Redirect method="POST">{again}</Redirect>')
    except KeyError:
        _pending.pop(call_id, None)
        return _xml(f"{_say('Sorry, something went wrong with this call. Please call again.')}<Hangup/>")
    except Exception as e:  # LLM/DB failure: let the caller repeat instead of dropping the call
        print(f"[voice] turn failed for {call_id}: {e}")
        _pending.pop(call_id, None)
        return _gather(call_id, "Sorry, I had a problem there. Could you say that again?")
    _pending.pop(call_id, None)
    return _respond(call_id, result["reply_text"], result["status"])


@router.post("/wait")
def wait(call_id: str = Query(...), key: str | None = Query(None)) -> Response:
    """Polled by SignalWire while the caller is on hold for the owner."""
    _check_key(key)
    result = call_engine.get_status(call_id)
    if result is None:
        return _xml("<Hangup/>")
    if result.get("reply_text"):
        return _respond(call_id, result["reply_text"], result["status"])
    if result["status"] != call_engine.WAITING_HUMAN:
        return _xml(f"{_say('Thanks for holding.')}<Hangup/>")
    again = escape(_url("wait", call_id=call_id))
    return _xml(f'<Pause length="{HOLD_SECONDS}"/><Redirect method="POST">{again}</Redirect>')


@router.post("/status")
def status(CallSid: str = Form(""), CallStatus: str = Form(""), key: str | None = Query(None)) -> dict:
    """Call ended: if the caller hung up mid-conversation, close the call so the owner gets a callback task."""
    _check_key(key)
    if CallStatus not in ("completed", "busy", "failed", "no-answer", "canceled"):
        return {"ok": True}
    call = db.find_call_by_sid(CallSid) if CallSid else None
    if not call:
        return {"ok": True}
    state = call["state_json"]
    if state.get("status") in (call_engine.COLLECTING, call_engine.WAITING_HUMAN):
        state["status"] = call_engine.NEEDS_HUMAN
        state["escalation"] = {**(state.get("escalation") or {}), "kind": "hung_up",
                               "reason": f"caller hung up before finishing ({config.OWNER_NAME} to call back)"}
        db.update_call(str(call["id"]), state, [{"role": "assistant", "text": "[caller hung up]"}])
        crm_sync.sync_call_in_background(str(call["id"]))
    return {"ok": True}
