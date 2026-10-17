"""One-turn-at-a-time conversation engine for browser calls.

Same pipeline as conversation_loop.run_conversation (transcribe -> safety check ->
extract_intent -> merge_state -> owner-approval check -> FAQ answer or
next_missing_field / book_appointment), but each HTTP request runs a single turn and
the state lives in talkse.calls.state_json instead of a local loop.

When the AI shouldn't decide alone (owner approval, a question nobody has answered
before) it hands off to the owner through handoff.py and the caller waits.

conversation_loop.py is not imported: it imports audio_playback, which needs
sounddevice and would crash inside Docker.
"""

import re
import time
import uuid

import booking_engine as be
import clinic_config as config
import crm_sync
import db
import faq
import handoff
from poc import transcribe_bytes, extract_intent, merge_state

# Call statuses
COLLECTING = "collecting"                # gathering details, the caller can talk
WAITING_HUMAN = handoff.WAITING_HUMAN    # on hold while the owner decides
DONE = "done"                            # booked / rescheduled / cancelled
NEEDS_HUMAN = "needs_human"              # owner calls back (callback, timeout, unclear)
EMERGENCY = "emergency"                  # safety instructions given, owner alerted, booking stopped
ACTIVE_STATUSES = (COLLECTING,)
TERMINAL_STATUSES = (DONE, NEEDS_HUMAN, EMERGENCY)

LLM_FALLBACK = {
    "intent": "unclear",
    "service": None,
    "issue": None,
    "area": None,
    "preferred_time": None,
    "caller_name": None,
    "existing_appointment_ref": None,
    "question": None,
    "confidence": 0.0,
}


def next_missing_field(state: dict) -> str | None:
    """Returns the name of the first missing required field for state['intent'], or None if complete."""
    intent = state.get("intent")
    if not intent or intent in ("unclear", "none", "null", "question"):
        return "intent"

    required_map = {
        "book": ["service", "area", "preferred_time", "caller_name", "caller_phone"],
        "reschedule": ["existing_appointment_ref", "preferred_time"],
        "cancel": ["existing_appointment_ref"],
    }

    for field in required_map.get(intent, []):
        if field == "caller_phone" and state.get("phone_declined"):
            continue
        val = state.get(field)
        if val is None or str(val).strip() == "" or str(val).lower() == "null":
            return field

    return None


def prompt_for_field(field: str) -> str:
    """Returns a natural question for a missing field."""
    prompts = {
        "intent": (
            f"Hi, thanks for calling {config.BUSINESS_NAME}! I can book a technician, "
            "help you reschedule or cancel a visit, or answer your questions. How can I help?"
        ),
        "service": "What can we help with? For example AC repair, a tune-up, plumbing, or electrical work.",
        "area": "What city or ZIP code is the job in?",
        "preferred_time": "What day and time works best for the visit?",
        "caller_name": "Great. And may I have your full name, please?",
        "caller_phone": "And what's the best phone number to reach you?",
        "existing_appointment_ref": "Could you tell me the name or the date your visit was booked under?",
    }
    return prompts.get(field, "Could you please provide more details?")


GREETING = prompt_for_field("intent")

_NAME_PREFIXES = re.compile(r"^(my name is|my name's|the name is|name is|this is|it's|it is|i am|i'm)\s+", re.I)


def _as_name(transcript: str) -> str | None:
    """A bare answer to "may I have your full name?" ("Riley Phonefix.") when the LLM misses it."""
    text = _NAME_PREFIXES.sub("", transcript.strip().strip(".!?, ")).strip(".!?, ")
    words = text.split()
    if 1 <= len(words) <= 4 and all(re.fullmatch(r"[A-Za-z][A-Za-z'\-]*", w) for w in words):
        return " ".join(w[:1].upper() + w[1:] for w in words)
    return None


_DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_DECLINE = re.compile(r"\b(no|nope|don'?t|rather not|prefer not|skip|not now)\b", re.I)


def _as_phone(transcript: str) -> str | None:
    """A phone number from speech: digits ("512-555-0199") or words ("five one two ...")."""
    tokens = re.findall(r"[a-z]+|\d+", transcript.lower())
    digits = "".join(_DIGIT_WORDS.get(t, t if t.isdigit() else "") for t in tokens)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+1{digits}"
    if 7 <= len(digits) <= 15:
        return digits
    return None


_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|correct|right|ok(ay)?|sounds good|go ahead|book it|please do|"
                  r"perfect|absolutely|confirm(ed)?|that works)\b", re.I)
_NO = re.compile(r"\b(no|nope|not|wrong|change|wait|actually|incorrect)\b", re.I)
_BOOKING_FIELDS = ("service", "area", "preferred_time", "caller_name", "caller_phone")


_DAY = re.compile(r"\b(today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                  r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}(st|nd|rd|th)?|"
                  r"\d{1,2}/\d{1,2})\b", re.I)
_CLOCK = re.compile(r"\b(\d{1,2}(:\d{2})?\s*(am|pm|a\.m\.|p\.m\.)|\d{1,2}:\d{2}|noon|midday|morning|afternoon|evening)",
                    re.I)


def _merge_time_change(old: str | None, new: str | None) -> str | None:
    """"Make it 11 AM" keeps the day already given; "make it Saturday" keeps the time."""
    if not old or not new or old == new:
        return new
    old_day, new_day = _DAY.search(old), _DAY.search(new)
    old_clock, new_clock = _CLOCK.search(old), _CLOCK.search(new)
    if new_clock and not new_day and old_day:
        return f"{old_day.group(0)} at {new}"
    if new_day and not new_clock and old_clock:
        return f"{new} at {old_clock.group(0)}"
    return new


def _booking_fields(state: dict) -> tuple:
    return tuple(state.get(f) for f in _BOOKING_FIELDS)


def _confirmation_prompt(state: dict) -> str:
    """Reads the booking back before it is made; an unclear time is asked again instead of guessed."""
    start = be.parse_time_string(state.get("preferred_time") or "")
    if start is None:
        state["preferred_time"] = None
        state["awaiting"] = "preferred_time"
        return "Sorry, I didn't catch the day and time. What day and time works best for the visit?"
    service = config.get_service(state.get("service") or "")
    when = start.strftime("%A, %B %d at %I:%M %p").replace(" 0", " ")
    phone = f", phone {state['caller_phone']}" if state.get("caller_phone") else ""
    state["awaiting"] = "confirm"
    return (f"Just to confirm: {service['name'] if service else state.get('service')} in {state.get('area')} "
            f"on {when}, for {state.get('caller_name')}{phone}. Shall I book it?")


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
        "issue": None,
        "area": None,
        "preferred_time": None,
        "caller_name": None,
        "caller_phone": caller_phone,  # phone calls: SignalWire's `From`; saved on the appointment
        "existing_appointment_ref": None,
        "owner_approved": False,
        "escalation": None,
        "turn_count": 0,
        "unclear_turns": 0,
        "status": COLLECTING,
        "idempotency_key": f"call_{int(time.time())}_{uuid.uuid4().hex[:6]}",
        "booking_result": None,
    }
    call = db.create_call(state, caller_phone)
    db.update_call(str(call["id"]), state, [{"role": "assistant", "text": GREETING}])
    return str(call["id"]), GREETING


def _follow_up(state: dict) -> str:
    """After answering a question: continue the booking if one is in progress, otherwise offer one."""
    if state.get("intent") in ("book", "reschedule", "cancel"):
        missing = next_missing_field(state)
        if missing:
            return prompt_for_field(missing)
        if state.get("intent") == "book" and not state.get("confirmed"):
            return _confirmation_prompt(state)
        return ""
    return "Is there anything else I can help with, or would you like to book a visit?"


def _execute_action(state: dict, call_id: str = None) -> str:
    """Runs the booking engine for a complete state, updates state['status'] and returns the reply."""
    intent = state["intent"]

    if intent == "book":
        result = be.book_appointment(state, state["idempotency_key"])
        state["booking_result"] = result
        if result["status"] == "confirmed":
            state["status"] = DONE
            return result["message"]
        if result.get("requires_human"):
            if call_id:
                return handoff.escalate(call_id, state, "approval", result["reason"])
            state["status"] = NEEDS_HUMAN
            return f"{result['reason']}. {config.OWNER_NAME} will call you back to confirm."
        # Slot problem (closed, technician off, already booked): ask for another time instead of ending the call
        state["preferred_time"] = None
        return f"{result['reason']} What other day and time works for you?"

    if intent == "reschedule":
        result = be.reschedule_appointment(state["existing_appointment_ref"], state["preferred_time"])
        state["booking_result"] = result
        if result["status"] == "rescheduled":
            state["status"] = DONE
            return result["message"]
        state["preferred_time"] = None
        return f"I couldn't reschedule that visit: {result['reason']} What other day and time works for you?"

    if intent == "cancel":
        result = be.cancel_appointment(state["existing_appointment_ref"])
        state["booking_result"] = result
        if result["status"] == "cancelled":
            state["status"] = DONE
            return result["message"]
        state["existing_appointment_ref"] = None
        return f"I couldn't cancel that visit: {result['reason']} Could you give me the name or date again?"

    state["intent"] = None
    return prompt_for_field("intent")


def handle_turn(call_id: str, audio_bytes: bytes, filename: str) -> dict:
    """Runs one caller turn from recorded audio (browser calls): transcribe, then handle_text_turn."""
    if not db.get_call(call_id):
        raise KeyError(f"Call {call_id} not found")
    return handle_text_turn(call_id, safe_transcribe_with_retry(audio_bytes, filename) or "")


def handle_text_turn(call_id: str, transcript: str) -> dict:
    """Runs one caller turn from text (phone calls arrive already transcribed by SignalWire).
    Returns transcript, reply_text and the call status."""
    call = db.get_call(call_id)
    if not call:
        raise KeyError(f"Call {call_id} not found")

    state = call["state_json"]
    if state.get("status") == WAITING_HUMAN:
        return {"transcript": "", "reply_text": f"I'm still checking with {config.OWNER_NAME}, one moment please.",
                "status": WAITING_HUMAN}
    if state.get("status") not in ACTIVE_STATUSES:
        return {"transcript": "", "reply_text": "This call has already ended.", "status": state.get("status")}

    state["turn_count"] = state.get("turn_count", 0) + 1

    # 1. Nothing heard
    if not transcript or not transcript.strip():
        reply_text = "Sorry, I didn't catch that. Could you say it again?"
        db.update_call(call_id, state, [{"role": "assistant", "text": reply_text}])
        return {"transcript": "", "reply_text": reply_text, "status": state["status"]}

    turns = [{"role": "caller", "text": transcript.strip()}]

    # 2. Safety override: never waits on booking logic or on the owner
    emergency_msg = be.check_emergency_protocol(transcript)
    if emergency_msg:
        state["status"] = EMERGENCY
        reply_text = emergency_msg
        handoff.alert_hazard(call_id, state, transcript)
    else:
        # 3. Extract intent and merge job details into the state
        before = _booking_fields(state)
        extracted = safe_extract_intent_with_retry(transcript)
        merge_state(state, extracted)
        state["preferred_time"] = _merge_time_change(before[2], state.get("preferred_time"))
        asked_question = extracted.get("intent") == "question" or bool(extracted.get("question"))
        # We just asked for the name and the LLM didn't extract one: take a short name-like answer as the name
        if state.get("awaiting") == "caller_name" and not state.get("caller_name") and not asked_question:
            state["caller_name"] = _as_name(transcript)
        # We just asked for a phone number (browser calls; phone calls already have it from caller ID)
        if state.get("awaiting") == "caller_phone" and not state.get("caller_phone") and not asked_question:
            phone = _as_phone(transcript)
            if phone:
                state["caller_phone"] = phone
            elif _DECLINE.search(transcript):
                state["phone_declined"] = True
        # Callers describe a problem ("my sink is leaking") instead of naming a service or saying "book":
        # derive the service from the problem, and treat it as a booking
        if not state.get("service") and state.get("issue"):
            matched = config.get_service(state["issue"])
            state["service"] = matched["name"] if matched else state["issue"]
        if state.get("intent") in (None, "unclear") and not asked_question and state.get("service"):
            state["intent"] = "book"

        # 4. Owner approval: installs, big estimates, commercial jobs, outside the service area
        service = config.get_service(state.get("service") or state.get("issue") or "")
        approval_reason = None if state.get("owner_approved") else be.check_owner_approval(
            service, transcript, state.get("area"))

        if approval_reason:
            reply_text = handoff.escalate(call_id, state, "approval", approval_reason)
        elif asked_question:
            # 5. Questions: answer from known FAQs, otherwise ask the owner live
            question = extracted.get("question") or transcript.strip()
            answer = faq.answer_question(question)
            if answer:
                state["unclear_turns"] = 0
                reply_text = f"{answer} {_follow_up(state)}".strip()
            else:
                reply_text = handoff.escalate(call_id, state, "question", question)
        else:
            if state["intent"] is None or state["intent"] == "unclear":
                state["unclear_turns"] = state.get("unclear_turns", 0) + 1
            else:
                state["unclear_turns"] = 0

            if state["unclear_turns"] >= 2:
                state["status"] = NEEDS_HUMAN
                state["escalation"] = {"kind": "unclear", "reason": "request unclear after two tries"}
                reply_text = (f"I'm having trouble understanding, so I'll have {config.OWNER_NAME} "
                              "or a team member call you back shortly.")
            else:
                # 6. Ask for the next missing field, or run the booking engine
                missing = next_missing_field(state)
                if missing:
                    state["awaiting"] = missing
                    reply_text = prompt_for_field(missing)
                elif state["intent"] == "book" and not state.get("confirmed"):
                    # Read the booking back first; book only on a clear yes with nothing changed
                    unchanged = _booking_fields(state) == before
                    if state.get("awaiting") == "confirm" and unchanged and _YES.search(transcript) \
                            and not _NO.search(transcript):
                        state["confirmed"] = True
                        state["awaiting"] = None
                        reply_text = _execute_action(state, call_id)
                    elif state.get("awaiting") == "confirm" and unchanged and _NO.search(transcript):
                        state["awaiting"] = None
                        reply_text = "No problem. What would you like to change: the service, the place, the time, or the name?"
                    else:
                        reply_text = _confirmation_prompt(state)
                else:
                    state["awaiting"] = None
                    reply_text = _execute_action(state, call_id)

    turns.append({"role": "assistant", "text": reply_text})
    db.update_call(call_id, state, turns)
    if state["status"] in TERMINAL_STATUSES:
        crm_sync.sync_call_in_background(call_id)
    return {"transcript": transcript.strip(), "reply_text": reply_text, "status": state["status"]}


def sweep_abandoned_calls(idle_minutes: int = 10) -> int:
    """Finalizes calls the caller walked away from (browser tab closed, phone status callback missed).

    On hold: the owner request times out as usual. Mid-conversation with something said: the
    owner gets a call-back task and CRM lead. Never-spoken or very old calls close quietly.
    MCP approval requests have no caller waiting, so they stay open for the owner.
    Returns how many calls were handed to the owner.
    """
    handed_over = 0
    for call in db.list_stale_calls(idle_minutes):
        call_id = str(call["id"])
        state = call["state_json"] or {}
        if state.get("channel") == "mcp":
            continue
        try:
            if state.get("status") == WAITING_HUMAN:
                handoff.check_call(call_id)  # past the hold timeout: times out and syncs to the CRM
                continue
            age_hours = (time.time() - call["started_at"].timestamp()) / 3600
            if not state.get("turn_count") or age_hours > 6:
                state["status"] = "abandoned"
                db.update_call(call_id, state, [{"role": "assistant", "text": "[call closed: no activity]"}])
                continue
            state["status"] = NEEDS_HUMAN
            state["escalation"] = {**(state.get("escalation") or {}), "kind": "hung_up",
                                   "reason": f"caller left before finishing ({config.OWNER_NAME} to call back)"}
            db.update_call(call_id, state, [{"role": "assistant", "text": "[caller left the call]"}])
            crm_sync.sync_call(call_id)
            handed_over += 1
        except Exception as e:
            print(f"[Sweeper] call {call_id} failed: {e}")
    return handed_over


def get_status(call_id: str) -> dict | None:
    """Call status for the page's polling; resolves owner handoffs (see handoff.check_call)."""
    return handoff.check_call(call_id)
