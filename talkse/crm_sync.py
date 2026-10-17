"""Writes finished Talkse calls into FlowCRM's tables (Prisma, "public" schema, same Neon database).

No FlowCRM API route is involved: Talkse inserts rows directly, and the FlowCRM UI
shows them because it reads the same tables.

Per finished call:
    caller           -> "Contact"   (matched by phone, then name; created if new)
    every call       -> "Lead"      (score: booked 90, needs owner 70, other 40) + CALL_LOGGED activity
    booking          -> "Deal"      in stage MEETING + MEETING_SCHEDULED activity
    needs_human /
    emergency        -> "Task"      priority HIGH, due in 2 hours

A CRM failure must never break a call: every entry point logs and returns.
"""

import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import clinic_config as config
import db

log = logging.getLogger("talkse.crm")

SOURCE = "Phone call (Talkse AI)"
SCORES = {"done": 90, "needs_human": 70}


def _ids() -> tuple[str | None, str | None]:
    return os.getenv("CRM_ORG_ID"), os.getenv("CRM_OWNER_USER_ID")


def _new_id(prefix: str) -> str:
    # Prisma uses cuid() by default; any unique string works for its String @id columns
    return f"{prefix}{uuid.uuid4().hex}"


def upsert_company(cur, org_id: str, name: str | None) -> str | None:
    """Returns the Company id for the caller's company (matched by name), creating it if needed."""
    if not name:
        return None
    cur.execute('SELECT id FROM "Company" WHERE "organizationId" = %s AND lower(name) = lower(%s) LIMIT 1;',
                (org_id, name))
    row = cur.fetchone()
    if row:
        return row[0]
    company_id = _new_id("co")
    cur.execute("""
        INSERT INTO "Company" (id, name, "organizationId", "updatedAt")
        VALUES (%s, %s, %s, now());
    """, (company_id, name, org_id))
    return company_id


def upsert_contact(cur, org_id: str, owner_id: str, name: str | None, phone: str | None,
                   company_id: str | None = None) -> str:
    """Returns the Contact id for this caller, creating the contact if needed."""
    if phone:
        cur.execute('SELECT id FROM "Contact" WHERE "organizationId" = %s AND phone = %s LIMIT 1;', (org_id, phone))
        row = cur.fetchone()
        if row:
            return row[0]
    if name:
        cur.execute('SELECT id FROM "Contact" WHERE "organizationId" = %s AND lower("fullName") = lower(%s) LIMIT 1;',
                    (org_id, name))
        row = cur.fetchone()
        if row:
            return row[0]

    contact_id = _new_id("ct")
    cur.execute("""
        INSERT INTO "Contact" (id, "fullName", phone, "organizationId", "ownerId", "companyId", "updatedAt")
        VALUES (%s, %s, %s, %s, %s, %s, now());
    """, (contact_id, name or "Unknown caller", phone, org_id, owner_id, company_id))
    return contact_id


def create_lead(cur, org_id: str, owner_id: str, contact_id: str, notes: str, score: int, status: str) -> str:
    lead_id = _new_id("ld")
    cur.execute("""
        INSERT INTO "Lead" (id, status, source, notes, score, "organizationId", "contactId", "assigneeId", "updatedAt")
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now());
    """, (lead_id, status, SOURCE, notes, score, org_id, contact_id, owner_id))
    return lead_id


def create_deal(cur, org_id: str, owner_id: str, contact_id: str, title: str, value: float,
                company_id: str | None = None) -> str:
    deal_id = _new_id("dl")
    cur.execute("""
        INSERT INTO "Deal" (id, title, value, stage, "organizationId", "contactId", "companyId", "assigneeId", "updatedAt")
        VALUES (%s, %s, %s, 'MEETING', %s, %s, %s, %s, now());
    """, (deal_id, title, value, org_id, contact_id, company_id, owner_id))
    return deal_id


def create_task(cur, org_id: str, owner_id: str, title: str, priority: str, contact_id: str, lead_id: str) -> str:
    task_id = _new_id("tk")
    cur.execute("""
        INSERT INTO "Task" (id, title, priority, "dueAt", "organizationId", "assigneeId", "contactId", "leadId", "updatedAt")
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now());
    """, (task_id, title, priority, datetime.now(timezone.utc) + timedelta(hours=2), org_id, owner_id, contact_id, lead_id))
    return task_id


def log_activity(cur, org_id: str, activity_type: str, message: str) -> None:
    cur.execute("""
        INSERT INTO "Activity" (id, type, message, "organizationId")
        VALUES (%s, %s, %s, %s);
    """, (_new_id("ac"), activity_type, message, org_id))


def _summary(state: dict) -> str:
    """One readable line for the Lead notes and Activity feed."""
    status = state.get("status")
    service = state.get("service") or "a service"
    booking = state.get("booking_result") or {}
    if status == "done" and booking.get("status") == "confirmed":
        return f"Booked {booking.get('service_name', service)} for {booking.get('scheduled_start', state.get('preferred_time'))}."
    if status == "done":
        return booking.get("message", f"Completed {state.get('intent')} request.")
    if status == "needs_human":
        esc = state.get("escalation") or {}
        kinds = {
            "callback": "Owner chose to call back",
            "timed_out": "Owner didn't answer in time, call back",
            "approval": "Needs owner approval",
            "question": "Asked a question the AI couldn't answer",
            "unclear": "AI couldn't understand the request",
        }
        return f"{kinds.get(esc.get('kind'), 'Needs owner follow-up')}: {esc.get('reason') or service}."
    if status == "emergency":
        return "SAFETY: caller reported a hazard, was told to leave and call 911; owner alerted."
    return f"Call ended while {status}."


def _transcript_text(transcript: list[dict]) -> str:
    return "\n".join(f"{'Caller' if t.get('role') == 'caller' else 'AI'}: {t.get('text', '')}" for t in transcript)


def _deal_value(service: dict | None) -> float:
    """Pipeline value: the expected engagement value, not the first meeting's price (a discovery call is free)."""
    if not service:
        return 0
    return float(service.get("estimated_deal_usd") or service.get("price_usd") or service.get("price_min") or 0)


def sync_call(call_id: str) -> None:
    """Writes one finished call into FlowCRM. Never raises."""
    org_id, owner_id = _ids()
    if not org_id or not owner_id:
        log.warning("CRM sync skipped: CRM_ORG_ID / CRM_OWNER_USER_ID not set")
        return

    try:
        call = db.get_call(call_id)
        if not call or call.get("crm_lead_id"):
            return  # unknown call, or already synced

        state = call["state_json"]
        status = state.get("status")
        name = state.get("caller_name") or call.get("caller_name")
        caller = name or "Unknown caller"
        summary = _summary(state)
        notes = f"{summary}\n\n--- Transcript ---\n{_transcript_text(call['transcript'])}"

        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                company_id = upsert_company(cur, org_id, state.get("company_name"))
                contact_id = upsert_contact(cur, org_id, owner_id, name, call.get("caller_phone"), company_id)
                lead_status = "QUALIFIED" if status == "done" else "CONTACTED" if status == "needs_human" else "NEW"
                lead_id = create_lead(cur, org_id, owner_id, contact_id, notes, SCORES.get(status, 40), lead_status)
                log_activity(cur, org_id, "CALL_LOGGED", f"Talkse AI call with {caller}: {summary}")

                booking = state.get("booking_result") or {}
                if status == "done" and booking.get("status") == "confirmed":
                    service = config.get_service(booking.get("appointment", {}).get("service_id") or state.get("service"))
                    who = f"{caller} ({state['area']})" if state.get("area") else caller
                    title = f"{booking.get('service_name', 'Booking')} · {who} · {booking.get('scheduled_start', '')}"
                    create_deal(cur, org_id, owner_id, contact_id, title, _deal_value(service), company_id)
                    log_activity(cur, org_id, "MEETING_SCHEDULED", f"Booked by Talkse AI: {title}")

                if status in ("needs_human", "emergency"):
                    prefix = "URGENT safety call" if status == "emergency" else "Call back"
                    create_task(cur, org_id, owner_id, f"{prefix}: {caller}. {summary}", "HIGH", contact_id, lead_id)

                cur.execute("UPDATE talkse.calls SET crm_contact_id = %s, crm_lead_id = %s WHERE id::text = %s;",
                            (contact_id, lead_id, str(call_id)))
            conn.commit()
            log.info("CRM sync done for call %s (lead %s)", call_id, lead_id)
        finally:
            conn.close()
    except Exception as e:
        log.error("CRM sync failed for call %s: %s", call_id, e)


def sync_call_in_background(call_id: str) -> None:
    """Runs sync_call on a daemon thread so the caller never waits on the CRM."""
    threading.Thread(target=sync_call, args=(call_id,), daemon=True).start()
