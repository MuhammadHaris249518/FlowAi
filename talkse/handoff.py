"""Human handoff: the AI asks the owner mid-call instead of guessing or hanging up.

Flow:
    1. call_engine detects an approval case or an unknown question -> escalate():
       a talkse.escalations row (pending), owner notified on every channel, call
       status "waiting_human", caller hears "one moment, let me check with <owner>".
    2. The owner answers on the owner console (/owner) or a future channel webhook
       (Telegram...) -> answer_escalation(): pending -> answered.
    3. The call page polls /api/calls/{id}/status -> check_call(): answered -> spoken,
       the decision becomes a natural reply and the conversation continues.
       No answer within HANDOFF_TIMEOUT_SECONDS -> timed_out, owner calls back later.

Safety hazards (Phase 8) are one-way: alert_hazard() records and pushes an alert,
the caller is never put on hold.
"""

import os
import time

import clinic_config as config
import db
import notifier

WAITING_HUMAN = "waiting_human"

APPROVAL_OPTIONS = [
    {"id": "approve", "label": "Approve, book it"},
    {"id": "site_visit", "label": "Free estimate visit first"},
    {"id": "callback", "label": "I'll call them"},
]
QUESTION_OPTIONS = [
    {"id": "callback", "label": "I'll call them"},
]


def _timeout_seconds() -> int:
    try:
        return int(os.getenv("HANDOFF_TIMEOUT_SECONDS", "45"))
    except ValueError:
        return 45


def _summary(state: dict, kind: str, reason: str) -> str:
    """What the owner reads: who, what job, where, when, and why it needs them."""
    parts = [f"Caller: {state.get('caller_name') or 'not given yet'}"]
    job = state.get("service") or state.get("issue")
    if job:
        parts.append(f"Job: {job}")
    if state.get("issue") and state.get("issue") != job:
        parts.append(f"Problem: {state['issue']}")
    if state.get("area"):
        parts.append(f"Area: {state['area']}")
    if state.get("preferred_time"):
        parts.append(f"Wants: {state['preferred_time']}")
    parts.append(f"Asks: {reason}" if kind == "question" else f"Why you: {reason}")
    return " | ".join(parts)


def escalate(call_id: str, state: dict, kind: str, reason: str) -> str:
    """Puts the caller on hold and asks the owner. Returns what the caller hears now."""
    options = APPROVAL_OPTIONS if kind == "approval" else QUESTION_OPTIONS
    escalation = db.create_escalation(
        call_id, kind, _summary(state, kind, reason),
        {"options": options, "question": reason if kind == "question" else None, "reason": reason},
    )
    notifier.notify_escalation(escalation)

    state["status"] = WAITING_HUMAN
    state["escalation"] = {"kind": kind, "reason": reason, "id": str(escalation["id"]), "started": time.time()}
    owner = config.OWNER_NAME
    if kind == "approval":
        return f"Let me quickly check with {owner}, our owner, because {reason}. One moment, please."
    return f"Good question. Let me check with {owner}, one moment please."


def alert_hazard(call_id: str, state: dict, transcript: str) -> None:
    """Safety hazard (Phase 8): record it and alert the owner immediately. Never blocks the call."""
    who = state.get("caller_name") or "Unknown caller"
    where = f" in {state['area']}" if state.get("area") else ""
    summary = f"SAFETY: {who}{where} said: \"{transcript.strip()[:200]}\""
    try:
        db.create_escalation(call_id, "hazard", summary, {"transcript": transcript}, status="alerted")
    except Exception as e:
        print(f"[Handoff] Could not record hazard: {e}")
    notifier.alert_owner(summary)


def answer_escalation(escalation_id: str, choice: str = None, text: str = None, save_as_faq: bool = False) -> dict:
    """The owner's decision, from the console or any channel webhook.

    choice: an option id (approve / site_visit / callback); text: a typed answer to a question.
    save_as_faq: store the typed answer so the AI answers that question itself next time.
    Returns {"ok": bool, "error"?: str}.
    """
    esc = db.get_escalation(escalation_id)
    if not esc:
        return {"ok": False, "error": "Escalation not found"}
    valid_choices = {o["id"] for o in (esc["options_json"] or {}).get("options", [])}
    if choice and choice not in valid_choices:
        return {"ok": False, "error": f"Unknown choice '{choice}'"}
    answer = choice or (text or "").strip()
    if not answer:
        return {"ok": False, "error": "Choose an option or type an answer"}

    updated = db.transition_escalation(escalation_id, "pending", "answered", answer)
    if not updated:
        return {"ok": False, "error": f"Already handled (status: {db.get_escalation(escalation_id)['status']})"}

    if save_as_faq and esc["kind"] == "question" and text and text.strip():
        question = (esc["options_json"] or {}).get("question") or esc["summary"]
        db.insert_faq(question, text.strip(), source="staff")
    return {"ok": True}


def save_answer_as_faq(escalation_id: str) -> dict:
    """Saves the owner's typed answer to a question as an FAQ, after the fact (Telegram's Save button)."""
    esc = db.get_escalation(escalation_id)
    if not esc or esc["kind"] != "question":
        return {"ok": False, "error": "Not a question"}
    answer = esc.get("answer") or ""
    if not answer or answer in {o["id"] for o in QUESTION_OPTIONS}:
        return {"ok": False, "error": "No typed answer to save"}
    question = (esc["options_json"] or {}).get("question") or esc["summary"]
    db.insert_faq(question, answer, source="staff")
    return {"ok": True}


def acknowledge(escalation_id: str) -> dict:
    """Owner acknowledges a safety alert."""
    updated = db.transition_escalation(escalation_id, "alerted", "acknowledged")
    return {"ok": bool(updated), **({} if updated else {"error": "Already acknowledged"})}


def _apply_owner_answer(state: dict, esc: dict) -> str:
    """Turns the owner's decision into what the caller hears, and continues the conversation."""
    # Imported here: call_engine imports this module at load time
    import call_engine as ce

    owner = config.OWNER_NAME
    answer = esc["answer"] or ""
    state["escalation"] = {**(state.get("escalation") or {}), "outcome": answer}

    if answer == "callback":
        state["status"] = ce.NEEDS_HUMAN
        state["escalation"]["kind"] = "callback"
        return f"{owner} would like to talk this through with you personally. He'll call you back within the hour."

    state["status"] = ce.COLLECTING
    if esc["kind"] == "approval":
        state["owner_approved"] = True
        if answer == "site_visit":
            state["service"] = "svc_site_visit"
            prefix = f"{owner} would like to start with a free estimate visit, so we can give you an exact quote."
        else:
            prefix = f"Good news, {owner} approved it."
        state["intent"] = state.get("intent") or "book"
        missing = ce.next_missing_field(state)
        rest = ce.prompt_for_field(missing) if missing else ce._execute_action(state)
        return f"{prefix} {rest}"

    # A typed answer to a question
    return f"{owner} says: {answer} {ce._follow_up(state)}".strip()


def check_call(call_id: str) -> dict | None:
    """Polled by the call page while the caller waits.

    Returns {"status"} while still waiting, or {"status", "reply_text", "new_reply": True}
    once the owner answered or the wait timed out. None if the call doesn't exist.
    """
    import call_engine as ce

    call = db.get_call(call_id)
    if not call:
        return None
    state = call["state_json"]
    if state.get("status") != WAITING_HUMAN:
        return {"status": state.get("status")}

    esc_id = (state.get("escalation") or {}).get("id")
    esc = db.get_escalation(esc_id) if esc_id else None
    reply = None

    if esc and esc["status"] == "answered":
        # answered -> spoken is atomic, so two polls in flight can't both speak the answer
        claimed = db.transition_escalation(esc_id, "answered", "spoken")
        if claimed:
            reply = _apply_owner_answer(state, claimed)
    elif esc and esc["status"] == "pending":
        waited = time.time() - (state.get("escalation") or {}).get("started", time.time())
        if waited > _timeout_seconds() and db.transition_escalation(esc_id, "pending", "timed_out"):
            state["status"] = ce.NEEDS_HUMAN
            state["escalation"]["kind"] = "timed_out"
            reply = (f"{config.OWNER_NAME} is with another customer right now. I've passed your request on, "
                     "and he'll call you back within two hours.")
    elif not esc:
        state["status"] = ce.NEEDS_HUMAN
        reply = f"I couldn't reach {config.OWNER_NAME} just now, so he'll call you back shortly."

    if reply is None:
        return {"status": WAITING_HUMAN}

    db.update_call(call_id, state, [{"role": "assistant", "text": reply}])
    if state["status"] in ce.TERMINAL_STATUSES:
        ce.crm_sync.sync_call_in_background(call_id)
    return {"status": state["status"], "reply_text": reply, "new_reply": True}
