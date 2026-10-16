import os
import json
from datetime import datetime
from dateutil import parser

# DATA_FILE lets us swap the business (clinic_data.json -> company_data.json) without code changes
CONFIG_PATH = os.path.join(os.path.dirname(__file__), os.getenv("DATA_FILE", "clinic_data.json"))

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CLINIC_DATA = json.load(f)

CLINIC = CLINIC_DATA.get("clinic", {})
PROVIDERS = CLINIC_DATA.get("providers", [])
SERVICES = CLINIC_DATA.get("services", [])
EMERGENCY_PROTOCOL = CLINIC_DATA.get("emergency_protocol", {})

def get_service(service_id_or_name: str) -> dict | None:
    """Finds a service by ID or fuzzy matching against service name/category."""
    if not service_id_or_name:
        return None
    query = str(service_id_or_name).lower().strip()
    
    # 1. Exact ID match
    for svc in SERVICES:
        if svc["id"].lower() == query:
            return svc
            
    # 2. Substring in name
    for svc in SERVICES:
        if query in svc["name"].lower() or svc["name"].lower() in query:
            return svc

    # 3. Match against service category
    for svc in SERVICES:
        cat = svc.get("category", "").lower()
        if cat and (query == cat or query in cat):
            return svc

    # 4. Keyword map with real valid IDs
    keywords = {
        "botox": "svc_botox_touchup",
        "consultation": "svc_consult",
        "consult": "svc_consult",
        "filler": "svc_filler_lips",
        "hydrafacial": "svc_hydrafacial",
        "laser": "svc_laser_small",
        "peel": "svc_peel"
    }
    for kw, default_id in keywords.items():
        if kw in query:
            return get_service(default_id)
            
    return None

def get_provider(provider_id_or_name: str) -> dict | None:
    """Finds a provider by ID or fuzzy name match."""
    if not provider_id_or_name:
        return None
    query = str(provider_id_or_name).lower().strip()
    
    for prov in PROVIDERS:
        if prov["id"].lower() == query or query in prov["name"].lower() or prov["name"].lower() in query:
            return prov
    return None

def providers_for_service(service_id: str) -> list[dict]:
    """Returns a list of providers who offer the specified service."""
    svc = get_service(service_id)
    if not svc:
        return []
    allowed_ids = set(svc.get("provider_ids", []))
    return [prov for prov in PROVIDERS if prov["id"] in allowed_ids]

def is_clinic_open(dt: datetime) -> bool:
    """Checks if the clinic is open on a given datetime."""
    day_name = dt.strftime("%A").lower()
    hours_info = CLINIC.get("hours", {}).get(day_name, {})
    if hours_info.get("closed", False):
        return False
        
    open_str = hours_info.get("open")
    close_str = hours_info.get("close")
    if not open_str or not close_str:
        return False
        
    open_time = datetime.strptime(open_str, "%H:%M").time()
    close_time = datetime.strptime(close_str, "%H:%M").time()
    
    return open_time <= dt.time() <= close_time

def provider_works_on(provider_id: str, dt_or_day: datetime | str) -> bool:
    """Checks if a provider works on a given day/datetime."""
    prov = get_provider(provider_id)
    if not prov:
        return False
        
    if isinstance(dt_or_day, datetime):
        day_str = dt_or_day.strftime("%A")
    else:
        day_str = str(dt_or_day).capitalize()
        
    working_days = [d.capitalize() for d in prov.get("working_days", [])]
    return day_str in working_days

if __name__ == "__main__":
    print(f"Loaded Clinic: {CLINIC.get('name')}")
    print(f"Service test (svc_consult): {get_service('svc_consult')['name']}")
    print(f"Provider test (prov_001): {get_provider('prov_001')['name']}")
    print(f"Providers for Botox: {[p['name'] for p in providers_for_service('svc_botox_touchup')]}")
