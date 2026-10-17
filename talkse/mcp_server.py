"""Talkse as an MCP server: the voice agent's abilities as tools other AI clients can call.

Mounted on the FastAPI app at /mcp (Streamable HTTP transport, stateless, JSON responses).
Clients: the FlowCRM AI Assistant (talkse-mcp-client.ts), Claude Desktop, Cursor, any MCP client.

Tools reuse the voice agent's own logic (booking_engine, handoff, faq, crm_sync), so a
booking made through MCP follows the same rules and lands in FlowCRM like a phone call.
"""

import time
import uuid

import os
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import booking_engine as be
import clinic_config as config
import crm_sync
import db
import faq
import handoff

mcp = FastMCP(
    "Talkse",
    instructions=(
        f"Tools for {config.BUSINESS_NAME}'s AI voice receptionist: business info, technician availability, "
        f"booking visits, recent calls and transcripts, owner ({config.OWNER_NAME}) approval requests, and FAQs."
    ),
    stateless_http=True,
    json_response=True,
    # The SDK's DNS-rebinding protection only allows localhost by default; this server is
    # public behind the platform's proxy. MCP_ALLOWED_HOSTS (comma-separated) re-enables a strict list.
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(os.getenv("MCP_ALLOWED_HOSTS")),
        allowed_hosts=[h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
        + ([urlparse(os.environ["PUBLIC_BASE_URL"]).netloc] if os.getenv("PUBLIC_BASE_URL") else []),
    ),
)
mcp.settings.streamable_http_path = "/"  # the app is mounted at /mcp


def _fmt_dt(value) -> str | None:
    return value.astimezone(config.TZ).strftime("%a %b %d, %I:%M %p") if value else None


@mcp.tool()
def get_business_info() -> dict:
    """Business profile: name, owner, hours, service area, services with prices, technicians."""
    return {
        "name": config.BUSINESS_NAME,
        "owner": config.OWNER_NAME,
        "local_time": config.now().strftime("%A %I:%M %p"),
        "open_now": config.is_clinic_open(config.now()),
        "hours": config.CLINIC.get("hours", {}),
        "service_area": config.CLINIC.get("service_area", []),
        "services": [{"id": s["id"], "name": s["name"], "price_usd": s.get("price_usd"),
                      "needs_owner_approval": bool(s.get("approval_required"))} for s in config.SERVICES],
        "technicians": [{"name": p["name"], "title": p.get("title"), "works": p.get("working_days")}
                        for p in config.PROVIDERS],
    }


@mcp.tool()
def check_availability(service: str, preferred_time: str, area: str = "") -> dict:
    """Check if a technician is free for a service at a time (e.g. "AC repair", "Thursday at 10 AM").
    Also reports if the job needs the owner's approval (installs, commercial, outside the service area)."""
    svc = config.get_service(service)
    if not svc:
        return {"available": False, "reason": f"Unknown service '{service}'"}
    start = be.parse_time_string(preferred_time)
    if start is None:
        return {"available": False, "reason": f"Couldn't understand the time '{preferred_time}'. Try e.g. 'Thursday at 10 AM'."}
    approval = be.check_owner_approval(svc, service, area or None)
    for tech in config.providers_for_service(svc["id"]):
        ok, reason = be.is_slot_available(tech["id"], svc["id"], start)
        if ok:
            return {"available": True, "service": svc["name"], "technician": tech["name"],
                    "time": start.strftime("%A, %B %d at %I:%M %p"), "needs_owner_approval": approval}
    return {"available": False, "service": svc["name"], "time": start.strftime("%A, %B %d at %I:%M %p"),
            "reason": reason, "needs_owner_approval": approval}


def _booking_key(customer_name: str, service: str, preferred_time: str, area: str) -> dict:
    """What the owner approved, so an approval can't be reused for a different job."""
    return {k: (v or "").strip().lower() for k, v in
            {"customer_name": customer_name, "service": service, "preferred_time": preferred_time, "area": area}.items()}


@mcp.tool()
def book_visit(customer_name: str, service: str, preferred_time: str, area: str, phone: str = "",
               approval_request_id: str = "") -> dict:
    """Book a technician visit and record it in the CRM (contact, lead, deal, activity).
    Jobs that need the owner's approval (installs, commercial, outside the service area) are NOT booked:
    the owner gets an approval request instead. After the owner approves it (owner console or email),
    call book_visit again with the same details and approval_request_id to book it."""
    booking = _booking_key(customer_name, service, preferred_time, area)
    state = {
        "intent": "book", "service": service, "issue": None, "area": area, "preferred_time": preferred_time,
        "caller_name": customer_name, "caller_phone": phone or None, "existing_appointment_ref": None,
        "owner_approved": False, "escalation": None, "turn_count": 0, "unclear_turns": 0, "status": "collecting",
        "idempotency_key": f"mcp_{int(time.time())}_{uuid.uuid4().hex[:6]}", "booking_result": None,
        "channel": "mcp", "mcp_booking": booking,
    }
    start = be.parse_time_string(preferred_time)
    if start is None:
        return {"booked": False, "reason": f"Couldn't understand the time '{preferred_time}'. Try e.g. 'Thursday at 10 AM'."}
    # Idempotent: an LLM client may call book_visit twice for one request; return the existing booking
    existing = db.find_booking(customer_name, start)
    if existing:
        tech = config.get_provider(existing["provider_id"])
        return {"booked": True, "already_booked": True,
                "message": f"Booking confirmed: {customer_name} with {tech['name'] if tech else 'a technician'} "
                           f"on {existing['scheduled_start'].astimezone(config.TZ).strftime('%A, %B %d at %I:%M %p')}."}

    # Human in the loop: only the owner can approve. The approval is checked against what he approved.
    call_id = None
    reason = be.check_owner_approval(config.get_service(service), service, area or None)
    if reason:
        if not approval_request_id:
            call = db.create_call(state, phone or None)
            call_id = str(call["id"])
            message = handoff.escalate(call_id, state, "approval", reason)
            db.update_call(call_id, state, [{"role": "assistant", "text": f"Approval requested via FlowCRM assistant: {reason}"}])
            return {"booked": False, "needs_owner_approval": True, "approval_request_id": state["escalation"]["id"],
                    "reason": reason,
                    "message": f"Not booked yet: {config.OWNER_NAME} must approve this job ({reason}). The request was "
                               f"sent to him. Once he approves, call book_visit again with approval_request_id."}
        esc = db.get_escalation(approval_request_id)
        if not esc or esc["kind"] != "approval":
            return {"booked": False, "reason": "Unknown approval request"}
        if esc["status"] == "pending":
            return {"booked": False, "needs_owner_approval": True, "reason": f"{config.OWNER_NAME} hasn't answered yet"}
        if esc.get("answer") != "approve":
            return {"booked": False, "reason": f"{config.OWNER_NAME} did not approve this job (answer: {esc.get('answer')})"}
        approved_call = db.get_call(str(esc["call_id"]))
        if not approved_call or (approved_call["state_json"] or {}).get("mcp_booking") != booking:
            return {"booked": False, "reason": "This approval was for a different job; request a new approval"}
        call_id = str(approved_call["id"])
        state = approved_call["state_json"]
        state["owner_approved"] = True

    result = be.book_appointment(state, state["idempotency_key"])
    if result["status"] != "confirmed":
        return {"booked": False, "reason": result.get("reason")}

    # Record it like a call so crm_sync writes the same CRM records a phone booking creates
    state["status"] = "done"
    state["booking_result"] = result
    if not call_id:
        call_id = str(db.create_call(state, phone or None)["id"])
    db.update_call(call_id, state, [{"role": "assistant", "text": f"Booked via FlowCRM assistant: {result['message']}"}])
    crm_sync.sync_call(call_id)
    return {"booked": True, "message": result["message"], "technician": result["provider_name"],
            "time": result["scheduled_start"], "deposit_required": result["deposit_required"]}


@mcp.tool()
def list_recent_calls(limit: int = 10) -> list[dict]:
    """Most recent calls to the voice agent: caller, status, what they wanted, when."""
    rows = db.list_calls(max(1, min(limit, 30)))
    out = []
    for r in rows:
        s = r["state_json"] or {}
        out.append({
            "call_id": str(r["id"]), "caller": r.get("caller_name") or s.get("caller_name") or "Unknown",
            "status": r["status"], "service": s.get("service"), "area": s.get("area"),
            "requested_time": s.get("preferred_time"),
            "booking": (s.get("booking_result") or {}).get("message"),
            "escalation": (s.get("escalation") or {}).get("kind"),
            "started": _fmt_dt(r.get("started_at")),
        })
    return out


@mcp.tool()
def get_call_transcript(call_id: str) -> dict:
    """Full transcript and outcome of one call."""
    call = db.get_call(call_id)
    if not call:
        return {"error": "Call not found"}
    return {"call_id": call_id, "status": call["status"], "caller": call.get("caller_name"),
            "transcript": call["transcript"], "started": _fmt_dt(call.get("started_at"))}


@mcp.tool()
def list_owner_requests(only_pending: bool = True) -> list[dict]:
    """Requests the AI escalated to the owner: approvals, caller questions, safety alerts."""
    rows = db.list_escalations(30)
    if only_pending:
        rows = [r for r in rows if r["status"] in ("pending", "alerted")]
    return [{"request_id": str(r["id"]), "kind": r["kind"], "status": r["status"], "summary": r["summary"],
             "answer": r.get("answer"), "created": _fmt_dt(r.get("created_at"))} for r in rows]


@mcp.tool()
def answer_owner_request(request_id: str, choice: str = "", answer_text: str = "", save_as_faq: bool = False) -> dict:
    """Answer a caller's pending QUESTION with answer_text (save_as_faq=true teaches the AI that answer).
    Job approvals can't be decided here: only the owner approves, from the owner console or the approval email."""
    esc = db.get_escalation(request_id)
    if not esc:
        return {"ok": False, "error": "Request not found"}
    if esc["kind"] == "approval":
        return {"ok": False, "error": f"Approvals must come from {config.OWNER_NAME} himself "
                                      "(owner console or the approval email), not from an AI assistant."}
    return handoff.answer_escalation(request_id, choice=choice or None, text=answer_text or None,
                                     save_as_faq=save_as_faq)


@mcp.tool()
def list_faqs() -> list[dict]:
    """FAQs the voice agent answers by itself: built-in ones plus answers the owner saved."""
    builtin = [{"question": f["question"], "answer": f["answer"], "source": "built-in"} for f in config.FAQ]
    staff = [{"question": f["question"], "answer": f["answer"], "source": "owner"} for f in db.get_faqs()]
    return builtin + staff


@mcp.tool()
def add_faq(question: str, answer: str) -> dict:
    """Teach the voice agent a new answer; callers asking this get it instantly from now on."""
    db.insert_faq(question, answer, source="staff")
    return {"saved": True}


@mcp.tool()
def answer_like_receptionist(question: str) -> dict:
    """What the voice agent would answer to a caller's question (from FAQs), or null if it would ask the owner."""
    return {"answer": faq.answer_question(question)}
