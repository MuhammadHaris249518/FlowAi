import os
import re
import json
from datetime import datetime, timedelta, time as dt_time
from dateutil import parser

import clinic_config as config
import db

def check_emergency_protocol(text: str) -> str | None:
    """Checks transcript for urgent/emergency keywords. Returns the caller guidance if triggered.

    Keywords and guidance come from the data file's emergency_protocol; the medspa
    list below is the fallback for clinic_data.json.
    """
    if not text:
        return None
    lower = text.lower()
    # "my smoke detector keeps beeping" is a low battery, not a fire: don't trigger on maintenance chirps
    maintenance = (re.search(r"\b(smoke|co|carbon monoxide)\s+(detector|alarm)s?\b", lower)
                   and re.search(r"\b(beep\w*|chirp\w*|batter(y|ies)|replace|install\w*)\b", lower)
                   and not re.search(r"\b(fire|flames?|burning|smell\w*)\b", lower))
    emergency_keywords = [] if maintenance else config.EMERGENCY_PROTOCOL.get("keywords") or [
        "difficulty breathing", "can't breathe", "cannot breathe",
        "severe swelling", "swelling up", "chest pain",
        "anaphylaxis", "allergic reaction"
    ]
    for kw in emergency_keywords:
        if re.search(rf"\b{re.escape(kw)}\b", lower):
            return (
                config.EMERGENCY_PROTOCOL.get("caller_instructions")
                or config.EMERGENCY_PROTOCOL.get(
                    "life_threatening_instructions",
                    "If you are experiencing a medical emergency such as difficulty breathing, severe swelling, or chest pain, please call 911 immediately."
                )
            )
    return None

def is_in_service_area(area: str | None) -> bool:
    """True if the caller's city/ZIP is in the data file's service area (unknown area counts as inside)."""
    if not area or not config.CLINIC.get("service_area"):
        return True
    lower = area.lower()
    if any(city.lower() in lower or lower in city.lower() for city in config.CLINIC["service_area"]):
        return True
    zip_match = re.search(r"\b(\d{5})\b", area)
    prefixes = config.CLINIC.get("service_area_zip_prefixes", [])
    return bool(zip_match and any(zip_match.group(1).startswith(p) for p in prefixes))


def check_owner_approval(service: dict | None, transcript: str, area: str | None = None) -> str | None:
    """Returns why this request needs the owner's decision, or None.

    Uses the data file's approval_rules: approval_required services, estimates over the
    limit, commercial keywords, and areas outside the service area. Without
    approval_rules (clinic_data.json) it falls back to the original medspa
    contraindication check.
    """
    rules = config.CLINIC.get("approval_rules")
    if not rules:
        return check_contraindications(service, transcript) if service else None

    if service and service.get("approval_required"):
        return f"every {service['name']} job is quoted by {config.OWNER_NAME} personally"

    threshold = rules.get("estimate_over_usd")
    if service and threshold is not None and service.get("price_usd", 0) > threshold:
        return f"{service['name']} is above our ${threshold:,} approval limit"

    lower = (transcript or "").lower()
    lower = re.sub(r"\b(home office|office hours|post office|at the store|from the store|store[- ]bought)\b", " ", lower)
    for kw in rules.get("keywords", []):
        if re.search(rf"\b{re.escape(kw)}\b", lower):
            return f"it sounds like a {kw} job, and {config.OWNER_NAME} reviews those personally"

    if not is_in_service_area(area):
        return f"{area} is outside our usual service area"
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
    """Parses fuzzy times ("Thursday at 4 PM", "tomorrow 10am") into a future, business-local datetime."""
    if not time_str:
        return None
    now = config.now()
    # dateutil ignores "tomorrow", so start from tomorrow's date; it fills missing parts from `default`
    base = now + timedelta(days=1) if "tomorrow" in time_str.lower() else now
    default = base.replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=None)
    try:
        dt = parser.parse(time_str, fuzzy=True, default=default)
    except Exception:
        return None  # unparseable ("sometime soon"): the caller is asked again, never booked at a guessed time
    # fuzzy parsing can return the default untouched for text with no date/time at all
    has_time_words = re.search(r"\d|tomorrow|today|tonight|morning|afternoon|evening|noon|"
                               r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                               r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", time_str.lower())
    if not has_time_words:
        return None
    dt = dt.replace(tzinfo=config.TZ)

    # Move times in the past into the future
    if dt < now:
        names_weekday = any(day in time_str.lower() for day in
                            ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))
        if dt.date() < now.date() or names_weekday:
            while dt < now:
                dt += timedelta(days=7)  # "Friday" said on a Friday evening -> next Friday
        else:
            dt += timedelta(days=1)  # earlier today, no weekday named -> same time tomorrow
    return dt

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
        return False, f"We're closed on {requested_start.strftime('%A')} at {requested_start.strftime('%I:%M %p').lstrip('0')}."
        
    duration = svc.get("duration_minutes", 30)
    scheduled_end = requested_start + timedelta(minutes=duration)
    
    # 4. Check Postgres overlapping appointments
    overlapping = db.get_overlapping_appointments(prov["id"], requested_start, scheduled_end, exclude_appt_id)
    if overlapping:
        return False, f"{prov['name']} already has a confirmed booking during that time slot."
        
    return True, "Slot is available"

def book_appointment(state: dict, idempotency_key: str) -> dict:
    """Executes booking logic against clinic data and Postgres."""
    # 1. Resolve service (fall back to the first service, e.g. the free discovery call)
    service = config.get_service(state.get("service") or "") or config.SERVICES[0]

    # 2. Owner-approval / safety check, skipped once the owner has approved this call
    if not state.get("owner_approved"):
        approval_reason = check_owner_approval(service, json.dumps(state, default=str), state.get("area"))
        if approval_reason:
            return {
                "status": "rejected",
                "reason": approval_reason,
                "requires_human": True
            }

    # 3. Resolve providers
    valid_providers = config.providers_for_service(service["id"])
    if not valid_providers:
        return {
            "status": "rejected",
            "reason": f"No providers available for {service['name']}.",
            "requires_human": True
        }

    # 4. Resolve requested time
    requested_start = parse_time_string(state.get("preferred_time") or "")
    if requested_start is None:
        return {"status": "rejected", "reason": "Sorry, I didn't catch the day and time."}

    # 5. First provider free at exactly that time; otherwise the original rule:
    #    first provider, moved to their next working day
    provider = next(
        (p for p in valid_providers if is_slot_available(p["id"], service["id"], requested_start)[0]),
        None,
    )
    if provider is None:
        provider = valid_providers[0]
        if not config.provider_works_on(provider["id"], requested_start):
            for offset in range(1, 8):
                test_dt = requested_start + timedelta(days=offset)
                if config.provider_works_on(provider["id"], test_dt) and config.is_clinic_open(test_dt):
                    requested_start = test_dt
                    break

    available, reason = is_slot_available(provider["id"], service["id"], requested_start)
    if not available:
        return {
            "status": "rejected",
            "reason": reason
        }

    duration = service.get("duration_minutes", 30)
    scheduled_end = requested_start + timedelta(minutes=duration)

    # 6. Deposit requirement rule from the data file
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
        deposit_required=deposit_required,
        caller_phone=state.get("caller_phone"),
    )
    
    return {
        "status": "confirmed",
        "appointment": appt,
        "service_name": service["name"],
        "provider_name": provider["name"],
        "scheduled_start": requested_start.strftime("%A, %b %d at %I:%M %p"),
        "deposit_required": deposit_required,
        "message": f"You're all set: {service['name']} with {provider['name']} on {requested_start.strftime('%A, %B %d at %I:%M %p').replace(' 0', ' ')}."
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
    if new_start is None:
        return {"status": "rejected", "reason": "Sorry, I didn't catch the new day and time."}
    
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
        notice_warning = f" (Note: Less than {notice_hours} hours notice given; deposit may be forfeited per our policy)."
        
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
