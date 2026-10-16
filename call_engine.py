"""One-turn-at-a-time conversation engine for browser calls.

Same pipeline as conversation_loop.run_conversation (transcribe -> emergency check ->
extract_intent -> contraindication check -> merge_state -> next_missing_field /
book_appointment), but each HTTP request runs a single turn and the state lives in
talkse.calls.state_json instead of a local loop.

conversation_loop.py is not imported: it imports audio_playback, which needs
sounddevice and would crash inside Docker.
"""

import time
import uuid

import booking_engine as be
import db
from poc import transcribe_bytes, extract_intent, merge_state

# Call statuses
COLLECTING = "collecting"      # still gathering booking details
DONE = "done"                  # action completed (booked / rescheduled / cancelled)
NEEDS_HUMAN = "needs_human"    # contraindication or repeated confusion (Phase 7 turns this into a live handoff)
EMERGENCY = "emergency"        # safety instructions given, booking stopped
ACTIVE_STATUSES = (COLLECTING,)

LLM_FALLBACK = {
    "intent": "unclear",
    "service": None,
    "preferred_time": None,
    "caller_name": None,
    "existing_appointment_ref": None,
    "confidence": 0.0,
}


def next_missing_field(state: dict) -> str | None:
    """Returns the name of the first missing required field for state['intent'], or None if complete."""
    intent = state.get("intent")
    if not intent or intent in ("unclear", "none", "null"):
        return "intent"

    required_map = {
        "book": ["service", "preferred_time", "caller_name"],
        "reschedule": ["existing_appointment_ref", "preferred_time"],
        "cancel": ["existing_appointment_ref"],
    }

    for field in required_map.get(intent, []):
        val = state.get(field)
        if val is None or str(val).strip() == "" or str(val).lower() == "null":
            return field

    return None


def prompt_for_field(field: str) -> str:
    """Returns a natural question for a missing field."""
    prompts = {
        "intent": "Hi! Are you looking to book, reschedule, or cancel an appointment today?",
        "service": "What service or treatment would you like to book?",
        "preferred_time": "What day and time works best for you?",
        "caller_name": "May I have your full name, please?",
        "existing_appointment_ref": "Could you please provide your existing appointment reference or date?",
    }
    return prompts.get(field, "Could you please provide more details?")


GREETING = prompt_for_field("intent")


def safe_transcribe_with_retry(audio_bytes: bytes, filename: str) -> str | None:
    """Wraps STT call in a retry block."""
    try:
        return transcribe_bytes(audio_bytes, filename)[0]
    except Exception as e:
        print(f"[STT Retry Warning] Transcribe attempt 1 failed: {e}. Retrying once...")
        time.sleep(1)
        try:
            return transcribe_bytes(audio_bytes, filename)[0]
        except Exception as retry_err:
            print(f"[STT Error] Transcribe failed permanently: {retry_err}")
            return None


def safe_extract_intent_with_retry(transcript: str) -> dict:
    """Wraps LLM call in a retry block."""
    try:
        return extract_intent(transcript)[0]
    except Exception as e:
        print(f"[LLM Retry Warning] Intent extraction attempt 1 failed: {e}. Retrying once...")
        time.sleep(1)
        try:
            return extract_intent(transcript)[0]
        except Exception as retry_err:
            print(f"[LLM Error] Intent extraction failed permanently: {retry_err}")
            return dict(LLM_FALLBACK)


def start_call(caller_phone: str = None) -> tuple[str, str]:
    """Creates a call and returns (call_id, greeting_text)."""
    state = {
        "intent": None,
        "service": None,
        "preferred_time": None,
        "caller_name": None,
        "existing_appointment_ref": None,
        "turn_count": 0,
        "unclear_turns": 0,
        "status": COLLECTING,
        "idempotency_key": f"call_{int(time.time())}_{uuid.uuid4().hex[:6]}",
        "booking_result": None,
    }
    call = db.create_call(state, caller_phone)
    db.update_call(str(call["id"]), state, [{"role": "assistant", "text": GREETING}])
    return str(call["id"]), GREETING


def _execute_action(state: dict) -> str:
    """Runs the booking engine for a complete state, updates state['status'] and returns the reply."""
    intent = state["intent"]

    if intent == "book":
        result = be.book_appointment(state, state["idempotency_key"])
        state["booking_result"] = result
        if result["status"] == "confirmed":
            state["status"] = DONE
            return result["message"]
        if result.get("requires_human"):
            state["status"] = NEEDS_HUMAN
            return result["reason"]
        # Slot problem (closed, provider off, already booked): ask for another time instead of ending the call
        state["preferred_time"] = None
        return f"{result['reason']} What other day and time works for you?"

    if intent == "reschedule":
        result = be.reschedule_appointment(state["existing_appointment_ref"], state["preferred_time"])
        state["booking_result"] = result
        if result["status"] == "rescheduled":
            state["status"] = DONE
            return result["message"]
        state["preferred_time"] = None
        return f"I couldn't reschedule that appointment: {result['reason']} What other day and time works for you?"

    if intent == "cancel":
        result = be.cancel_appointment(state["existing_appointment_ref"])
        state["booking_result"] = result
        if result["status"] == "cancelled":
            state["status"] = DONE
            return result["message"]
        state["existing_appointment_ref"] = None
        return f"I couldn't cancel that appointment: {result['reason']} Could you give me the reference again?"

    state["intent"] = None
    return prompt_for_field("intent")


def handle_turn(call_id: str, audio_bytes: bytes, filename: str) -> dict:
    """Runs one caller turn. Returns transcript, reply_text and the call status."""
    call = db.get_call(call_id)
    if not call:
        raise KeyError(f"Call {call_id} not found")

    state = call["state_json"]
    if state.get("status") not in ACTIVE_STATUSES:
        return {"transcript": "", "reply_text": "This call has already ended.", "status": state.get("status")}

    state["turn_count"] = state.get("turn_count", 0) + 1

    # 1. Transcribe audio
    transcript = safe_transcribe_with_retry(audio_bytes, filename)
    if not transcript or not transcript.strip():
        reply_text = "Sorry, I didn't catch that. Could you say it again?"
        db.update_call(call_id, state, [{"role": "assistant", "text": reply_text}])
        return {"transcript": "", "reply_text": reply_text, "status": state["status"]}

    turns = [{"role": "caller", "text": transcript.strip()}]

    # 2. Emergency override: never waits on booking logic
    emergency_msg = be.check_emergency_protocol(transcript)
    if emergency_msg:
        state["status"] = EMERGENCY
        reply_text = emergency_msg
    else:
        # 3. Extract intent
        extracted = safe_extract_intent_with_retry(transcript)

        # 4. Contraindication check
        svc_candidate = be.config.get_service(state.get("service") or extracted.get("service") or "")
        contra_reason = be.check_contraindications(svc_candidate, transcript)
        if contra_reason:
            state["status"] = NEEDS_HUMAN
            reply_text = contra_reason
        else:
            # 5. Merge into state
            merge_state(state, extracted)

            if state["intent"] is None or state["intent"] == "unclear":
                state["unclear_turns"] = state.get("unclear_turns", 0) + 1
            else:
                state["unclear_turns"] = 0

            if state["unclear_turns"] >= 2:
                state["status"] = NEEDS_HUMAN
                reply_text = "I'm having trouble understanding your request. Let me get a team member to help you."
            else:
                # 6. Ask for the next missing field, or run the booking engine
                missing = next_missing_field(state)
                reply_text = prompt_for_field(missing) if missing else _execute_action(state)

    turns.append({"role": "assistant", "text": reply_text})
    db.update_call(call_id, state, turns)
    return {"transcript": transcript.strip(), "reply_text": reply_text, "status": state["status"]}


def get_status(call_id: str) -> dict | None:
    """Returns the call status and transcript, or None if the call does not exist."""
    call = db.get_call(call_id)
    if not call:
        return None
    return {"status": call["status"], "transcript": call["transcript"]}
