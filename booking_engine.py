import os
import json
from datetime import datetime, timedelta, time as dt_time
from dateutil import parser

import clinic_config as config
import db

def check_emergency_protocol(text: str) -> str | None:
    """Checks transcript for emergency keywords. Returns emergency guidance if triggered."""
    if not text:
        return None
    lower = text.lower()
    emergency_keywords = [
        "difficulty breathing", "can't breathe", "cannot breathe",
        "severe swelling", "swelling up", "chest pain",
        "anaphylaxis", "allergic reaction"
    ]
    for kw in emergency_keywords:
        if kw in lower:
            return config.EMERGENCY_PROTOCOL.get(
                "life_threatening_instructions",
                "If you are experiencing a medical emergency such as difficulty breathing, severe swelling, or chest pain, please call 911 immediately."
            )
    return None

def check_contraindications(service: dict, transcript_or_state: str | dict) -> str | None:
    """Checks if requested service has contraindications matching caller text/state."""
    if not service:
        return None
        
    text_to_check = ""
    if isinstance(transcript_or_state, str):
        text_to_check = transcript_or_state.lower()
    elif isinstance(transcript_or_state, dict):
        text_to_check = json.dumps(transcript_or_state).lower()
        
    contraindications = service.get("contraindications", [])
    
    # Check general high-risk conditions: pregnancy, breastfeeding, accutane
    risk_terms = {
        "pregnant": ["pregnant", "pregnancy", "expecting"],
        "breastfeeding": ["breastfeeding", "nursing", "lactating"],
        "accutane": ["accutane", "isotretinoin"]
    }
    
    for condition, keywords in risk_terms.items():
        if any(kw in text_to_check for kw in keywords):
            # Botox, fillers, lasers, peels, coolsculpting are contraindicated
            if service.get("category") in ("injectable", "laser", "body_contouring", "peel") or contraindications:
                return f"For safety reasons, {service['name']} cannot be performed during {condition}. This request has been flagged for human provider review."
                
    return None

def parse_time_string(time_str: str) -> datetime | None:
    """Helper to parse fuzzy relative or explicit time strings into a future datetime."""
    if not time_str:
        return None
    try:
        dt = parser.parse(time_str, fuzzy=True)
        now = datetime.now()
        # If parsed time is in the past, adjust day/year to future
        if dt < now:
            if dt.time() > now.time() and dt.date() < now.date():
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
            if dt < now:
                dt = dt + timedelta(days=1)
        return dt
    except Exception:
        # Fallback default: Tomorrow at 14:00 if unparseable
        now = datetime.now()
        return (now + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)

def is_slot_available(provider_id: str, service_id: str, requested_start: datetime, exclude_appt_id: str = None) -> tuple[bool, str]:
    """Checks clinic hours, provider working days, service offering, and existing appointments."""
    svc = config.get_service(service_id)
    if not svc:
        return False, f"Service '{service_id}' was not found."
        
    prov = config.get_provider(provider_id)
    if not prov:
        return False, f"Provider '{provider_id}' was not found."
        
    # 1. Check provider supports service
    if prov["id"] not in svc.get("provider_ids", []):
        return False, f"{prov['name']} does not perform {svc['name']}."
        
    # 2. Check provider working days
    if not config.provider_works_on(prov["id"], requested_start):
        working_days = ", ".join(prov.get("working_days", []))
        return False, f"{prov['name']} does not work on {requested_start.strftime('%A')}s (works {working_days})."
        
    # 3. Check clinic hours
    if not config.is_clinic_open(requested_start):
        return False, f"The clinic is closed on {requested_start.strftime('%A')} at {requested_start.strftime('%I:%M %p')}."
        
    duration = svc.get("duration_minutes", 30)
    scheduled_end = requested_start + timedelta(minutes=duration)
    
    # 4. Check Postgres overlapping appointments
    overlapping = db.get_overlapping_appointments(prov["id"], requested_start, scheduled_end, exclude_appt_id)
    if overlapping:
        return False, f"{prov['name']} already has a confirmed booking during that time slot."
        
    return True, "Slot is available"

def book_appointment(state: dict, idempotency_key: str) -> dict:
    """Executes booking logic against clinic data and Postgres."""
    # 1. Resolve service
    svc_input = state.get("service") or "botox"
    service = config.get_service(svc_input)
    if not service:
        service = config.get_service("svc_botox_touchup")
        
    # 2. Safety / Contraindication check
    contra_reason = check_contraindications(service, state)
    if contra_reason:
        return {
            "status": "rejected",
            "reason": contra_reason,
            "requires_human": True
        }
        
    # 3. Resolve provider
    valid_providers = config.providers_for_service(service["id"])
    if not valid_providers:
        return {
            "status": "rejected",
            "reason": f"No providers available for {service['name']}.",
            "requires_human": True
        }
        
    # Pick requested provider or first available
    provider = valid_providers[0]
    
    # 4. Resolve requested time
    time_input = state.get("preferred_time") or "tomorrow at 2pm"
    requested_start = parse_time_string(time_input)
    
    # Ensure requested start falls on a valid provider day
    if not config.provider_works_on(provider["id"], requested_start):
        # Find next valid working day for provider
        for offset in range(1, 8):
            test_dt = requested_start + timedelta(days=offset)
            if config.provider_works_on(provider["id"], test_dt) and config.is_clinic_open(test_dt):
                requested_start = test_dt
                break

    # 5. Check availability
    available, reason = is_slot_available(provider["id"], service["id"], requested_start)
    if not available:
        return {
            "status": "rejected",
            "reason": reason
        }
        
    duration = service.get("duration_minutes", 30)
    scheduled_end = requested_start + timedelta(minutes=duration)
    
    # 6. Deposit requirement rule from clinic_data.json
    deposit_threshold = config.CLINIC.get("deposit_policy", {}).get("applies_to_services_priced_over_usd", 200)
    service_price = service.get("price_usd", 0)
    deposit_required = service_price >= deposit_threshold
    
    caller_name = state.get("caller_name") or "Valued Client"
    
    # 7. Insert row into Postgres
    appt = db.insert_appointment(
        idempotency_key=idempotency_key,
        service_id=service["id"],
        provider_id=provider["id"],
        caller_name=caller_name,
        scheduled_start=requested_start,
        scheduled_end=scheduled_end,
        deposit_required=deposit_required
    )
    
    return {
        "status": "confirmed",
        "appointment": appt,
        "service_name": service["name"],
        "provider_name": provider["name"],
        "scheduled_start": requested_start.strftime("%A, %b %d at %I:%M %p"),
        "deposit_required": deposit_required,
        "message": f"Successfully booked {service['name']} with {provider['name']} for {requested_start.strftime('%A, %b %d at %I:%M %p')}."
    }

def reschedule_appointment(existing_ref: str, new_time_str: str) -> dict:
    """Reschedules an existing appointment to a new time."""
    appt = db.get_appointment(existing_ref)
    if not appt:
        return {
            "status": "rejected",
            "reason": f"No active appointment found for reference '{existing_ref}'."
        }
        
    service = config.get_service(appt["service_id"])
    provider = config.get_provider(appt["provider_id"])
    
    new_start = parse_time_string(new_time_str)
    
    # Check availability for new time
    available, reason = is_slot_available(provider["id"], service["id"], new_start, exclude_appt_id=str(appt["id"]))
    if not available:
        return {
            "status": "rejected",
            "reason": f"Cannot reschedule to that time: {reason}"
        }
        
    duration = service.get("duration_minutes", 30)
    new_end = new_start + timedelta(minutes=duration)
    
    # Check 24h notice policy window
    notice_hours = config.CLINIC.get("cancellation_policy", {}).get("notice_required_hours", 24)
    hours_until_appt = (appt["scheduled_start"] - datetime.now(appt["scheduled_start"].tzinfo)).total_seconds() / 3600.0
    
    notice_warning = ""
    if hours_until_appt < notice_hours:
        notice_warning = f" (Note: Less than {notice_hours} hours notice given; deposit may be forfeited per clinic policy)."
        
    updated = db.update_appointment_time(appt["id"], new_start, new_end)
    
    return {
        "status": "rescheduled",
        "appointment": updated,
        "new_start": new_start.strftime("%A, %b %d at %I:%M %p"),
        "message": f"Rescheduled appointment for {updated['caller_name']} to {new_start.strftime('%A, %b %d at %I:%M %p')}.{notice_warning}"
    }

def cancel_appointment(existing_ref: str) -> dict:
    """Cancels an existing appointment."""
    appt = db.get_appointment(existing_ref)
    if not appt:
        return {
            "status": "rejected",
            "reason": f"No active appointment found for reference '{existing_ref}'."
        }
        
    cancelled = db.cancel_appointment(appt["id"])
    return {
        "status": "cancelled",
        "appointment": cancelled,
        "message": f"Appointment reference '{existing_ref}' for {cancelled['caller_name']} has been cancelled."
    }

if __name__ == "__main__":
    db.init_db()
    test_key = "test_key_001"
    res = book_appointment({
        "service": "botox",
        "preferred_time": "next Tuesday at 2pm",
        "caller_name": "Sarah Connor"
    }, idempotency_key=test_key)
    print("Booking Result:", json.dumps(res, indent=2, default=str))
