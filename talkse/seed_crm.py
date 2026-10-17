"""Seeds FlowCRM (Prisma tables in the "public" schema) with demo data for Cool Breeze Home Services.

Run once after `npx prisma db push` has created FlowCRM's tables:
    python seed_crm.py            # add demo data (safe to re-run)
    python seed_crm.py --reset    # first delete the demo org, its users and all talkse test calls

IDs are fixed so the env vars are known in advance:
    CRM_ORG_ID=org_coolbreeze  CRM_OWNER_USER_ID=usr_haris  DEMO_CLERK_ID=demo_haris
"""

import json
import sys
from datetime import datetime, timedelta, timezone

import db

ORG_ID = "org_coolbreeze"
ORG_NAME = "Cool Breeze Home Services"
OWNER_ID = "usr_haris"
OWNER_CLERK_ID = "demo_haris"
OWNER_NAME = "Haris (Owner)"
OLD_USER_IDS = ["usr_ahmed"]  # earlier demo owner

COMPANIES = [
    ("co_01", "Lamar Dental Studio", "Healthcare"),
    ("co_02", "Barton Creek Property Management", "Real estate"),
    ("co_03", "Eastside Coffee Co.", "Restaurant"),
]

# (id, name, phone, email, company)
CONTACTS = [
    ("ct_01", "Emily Carter", "512-555-0101", "emily.carter@example.com", None),
    ("ct_02", "Jacob Martinez", "512-555-0102", "jacob.martinez@example.com", None),
    ("ct_03", "Olivia Nguyen", "512-555-0103", "olivia.nguyen@example.com", None),
    ("ct_04", "Daniel Brooks", "512-555-0104", "daniel.brooks@example.com", "co_01"),
    ("ct_05", "Sophia Patel", "512-555-0105", "sophia.patel@example.com", "co_02"),
    ("ct_06", "Ethan Walker", "512-555-0106", "ethan.walker@example.com", None),
    ("ct_07", "Ava Thompson", "512-555-0107", "ava.thompson@example.com", None),
    ("ct_08", "Noah Kim", "512-555-0108", "noah.kim@example.com", "co_03"),
]

# (id, contact, status, source, score, notes)
LEADS = [
    ("ld_01", "ct_01", "NEW", "Phone call", 55, "AC blowing warm air, Round Rock. Wants a visit this week."),
    ("ld_02", "ct_02", "CONTACTED", "Website form", 62, "Water heater not heating, Pflugerville."),
    ("ld_03", "ct_03", "QUALIFIED", "Referral", 80, "Wants a quote to replace a 15-year-old AC, Cedar Park."),
    ("ld_04", "ct_04", "NEW", "Phone call", 45, "Dental office: two rooftop units need service."),
    ("ld_05", "ct_05", "QUALIFIED", "Google Ads", 75, "Property manager: 12-unit tune-up contract."),
    ("ld_06", "ct_06", "CONTACTED", "Yelp", 50, "Breaker keeps tripping in the kitchen, Austin 78704."),
    ("ld_07", "ct_07", "DISQUALIFIED", "Phone call", 15, "Outside service area (San Antonio)."),
    ("ld_08", "ct_08", "CONVERTED", "Referral", 90, "Coffee shop walk-in cooler drain fixed; repeat customer."),
]

# (id, contact, company, title, value USD, stage, closed days ago)
DEALS = [
    ("dl_01", "ct_01", None, "AC Repair · Emily Carter (Round Rock)", 350, "MEETING", None),
    ("dl_02", "ct_02", None, "Water Heater Repair · Jacob Martinez", 450, "PROPOSAL", None),
    ("dl_03", "ct_03", None, "AC Replacement · Olivia Nguyen (Cedar Park)", 7200, "NEGOTIATION", None),
    ("dl_04", "ct_05", "co_02", "12-unit tune-up contract · Barton Creek PM", 9600, "QUALIFIED", None),
    ("dl_05", "ct_08", "co_03", "Cooler drain repair · Eastside Coffee", 600, "WON", 3),
    ("dl_06", "ct_07", None, "AC Repair · Ava Thompson (San Antonio)", 350, "LOST", 5),
]

# (id, title, priority, contact, lead, due in hours)
TASKS = [
    ("tk_01", "Call Emily to confirm Thursday visit window", "HIGH", "ct_01", "ld_01", 3),
    ("tk_02", "Send replacement quote to Olivia", "MEDIUM", "ct_03", "ld_03", 24),
    ("tk_03", "Site survey for Barton Creek tune-up contract", "MEDIUM", "ct_05", "ld_05", 48),
    ("tk_04", "Restock R-410A refrigerant", "LOW", None, None, 72),
]

ACTIVITIES = [
    ("CALL_LOGGED", "Call logged with Emily Carter (AC blowing warm air)", 2),
    ("LEAD_CREATED", "New lead from website form: Jacob Martinez", 6),
    ("DEAL_STAGE_CHANGED", "AC Replacement · Olivia Nguyen moved to Negotiation", 20),
    ("MEETING_SCHEDULED", "Estimate visit scheduled with Olivia Nguyen", 26),
]


# Ready-made automation workflows (active). Talkse calls emit LEAD_CREATED / DEAL_STAGE_CHANGED,
# and moving a deal in the Pipeline emits DEAL_STAGE_CHANGED, so these run on real data.
# (id, name, [(node id, type, config, x, y)], [(source, target, label)])
WORKFLOWS = [
    ("wf_hot_lead", "Hot caller lead → fast call-back", [
        ("wn_hot_1", "TRIGGER", {"trigger": "LEAD_CREATED"}, 250, 0),
        ("wn_hot_2", "CONDITION", {"field": "entity.score", "op": "gte", "value": "70"}, 250, 150),
        ("wn_hot_3", "ACTION_STATIC", {"action": "CREATE_TASK", "title": "Call {{contactName}} to confirm the job details",
                                        "priority": "HIGH"}, 250, 300),
        ("wn_hot_4", "ACTION_STATIC", {"action": "NOTIFY_OWNER", "subject": "Hot lead: {{contactName}}",
                                        "message": "A new lead scored {{leadScore}}/100 ({{leadSource}}). "
                                                   "A call-back task was added to your Tasks."}, 250, 450),
    ], [("wn_hot_1", "wn_hot_2", None), ("wn_hot_2", "wn_hot_3", "true"), ("wn_hot_3", "wn_hot_4", None)]),
    ("wf_booking_email", "Booked visit → AI confirmation email", [
        ("wn_bk_1", "TRIGGER", {"trigger": "DEAL_STAGE_CHANGED"}, 250, 0),
        ("wn_bk_2", "CONDITION", {"field": "entity.toStage", "op": "eq", "value": "MEETING"}, 250, 150),
        ("wn_bk_3", "ACTION_AI", {"instructions": "Write a warm confirmation email to {{contactName}} for their booked "
                                                  "visit: {{dealTitle}}. Say the technician will call 30 minutes before "
                                                  "arriving. Sign off as Cool Breeze Home Services."}, 250, 300),
    ], [("wn_bk_1", "wn_bk_2", None), ("wn_bk_2", "wn_bk_3", "true")]),
    ("wf_big_job", "High-value job → owner alert", [
        ("wn_big_1", "TRIGGER", {"trigger": "DEAL_STAGE_CHANGED"}, 250, 0),
        ("wn_big_2", "CONDITION", {"field": "entity.value", "op": "gte", "value": "1000"}, 250, 150),
        ("wn_big_3", "ACTION_STATIC", {"action": "NOTIFY_OWNER", "subject": "High-value job ({{dealValue}})",
                                        "message": "{{dealTitle}} is now in {{dealStage}}. "
                                                   "Worth a personal call from the owner."}, 250, 300),
    ], [("wn_big_1", "wn_big_2", None), ("wn_big_2", "wn_big_3", "true")]),
]


def seed_workflows(cur) -> None:
    for wid, name, nodes, edges in WORKFLOWS:
        cur.execute("""
            INSERT INTO "Workflow" (id, name, "organizationId", "isActive", "updatedAt")
            VALUES (%s, %s, %s, true, now())
            ON CONFLICT DO NOTHING;
        """, (wid, name, ORG_ID))
        for nid, ntype, config, x, y in nodes:
            cur.execute("""
                INSERT INTO "WorkflowNode" (id, "workflowId", type, config, "positionX", "positionY")
                VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT DO NOTHING;
            """, (nid, wid, ntype, json.dumps(config), x, y))
        for i, (source, target, label) in enumerate(edges, start=1):
            cur.execute("""
                INSERT INTO "WorkflowEdge" (id, "workflowId", "sourceNodeId", "targetNodeId", label)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING;
            """, (f"{wid}_e{i}", wid, source, target, label))


def reset() -> None:
    """Deletes the demo org (contacts, leads, deals, tasks, activities cascade), demo users and talkse test data."""
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM "Organization" WHERE id = %s;', (ORG_ID,))
            cur.execute('DELETE FROM "User" WHERE id = ANY(%s);', ([OWNER_ID, *OLD_USER_IDS],))
            cur.execute("DELETE FROM talkse.escalations;")
            cur.execute("DELETE FROM talkse.calls;")
            cur.execute("DELETE FROM talkse.appointments;")
            cur.execute("DELETE FROM talkse.faqs;")
        conn.commit()
        print("[Seed] Reset: demo org, demo users and talkse test data removed")
    finally:
        conn.close()


def seed() -> None:
    now = datetime.now(timezone.utc)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO "Organization" (id, name, slug, "updatedAt")
                VALUES (%s, %s, 'cool-breeze', now())
                ON CONFLICT DO NOTHING;
            """, (ORG_ID, ORG_NAME))
            cur.execute("""
                INSERT INTO "User" (id, "clerkId", email, "fullName")
                VALUES (%s, %s, 'haris@coolbreeze.example', %s)
                ON CONFLICT DO NOTHING;
            """, (OWNER_ID, OWNER_CLERK_ID, OWNER_NAME))
            cur.execute("""
                INSERT INTO "Membership" (id, role, "userId", "organizationId")
                VALUES ('mb_haris', 'ORG_OWNER', %s, %s)
                ON CONFLICT DO NOTHING;
            """, (OWNER_ID, ORG_ID))

            for cid, name, industry in COMPANIES:
                cur.execute("""
                    INSERT INTO "Company" (id, name, industry, "organizationId", "updatedAt")
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT DO NOTHING;
                """, (cid, name, industry, ORG_ID))

            for cid, name, phone, email, company in CONTACTS:
                cur.execute("""
                    INSERT INTO "Contact" (id, "fullName", phone, email, "organizationId", "companyId", "ownerId", "updatedAt")
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT DO NOTHING;
                """, (cid, name, phone, email, ORG_ID, company, OWNER_ID))

            for lid, cid, status, source, score, notes in LEADS:
                cur.execute("""
                    INSERT INTO "Lead" (id, status, source, notes, score, "organizationId", "contactId", "assigneeId", "updatedAt")
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT DO NOTHING;
                """, (lid, status, source, notes, score, ORG_ID, cid, OWNER_ID))

            for did, cid, company, title, value, stage, closed_days in DEALS:
                closed_at = now - timedelta(days=closed_days) if closed_days else None
                lost_reason = "NOT_A_FIT" if stage == "LOST" else None
                cur.execute("""
                    INSERT INTO "Deal" (id, title, value, stage, "organizationId", "contactId", "companyId", "assigneeId", "closedAt", "lostReason", "updatedAt")
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT DO NOTHING;
                """, (did, title, value, stage, ORG_ID, cid, company, OWNER_ID, closed_at, lost_reason))

            for tid, title, priority, cid, lid, due_hours in TASKS:
                cur.execute("""
                    INSERT INTO "Task" (id, title, priority, "dueAt", "organizationId", "assigneeId", "contactId", "leadId", "updatedAt")
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT DO NOTHING;
                """, (tid, title, priority, now + timedelta(hours=due_hours), ORG_ID, OWNER_ID, cid, lid))

            for i, (atype, message, hours_ago) in enumerate(ACTIVITIES, start=1):
                cur.execute("""
                    INSERT INTO "Activity" (id, type, message, "organizationId", "actorId", "createdAt")
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING;
                """, (f"ac_seed_{i:02d}", atype, message, ORG_ID, OWNER_ID, now - timedelta(hours=hours_ago)))

            seed_workflows(cur)
        conn.commit()
        print(f"[Seed] FlowCRM demo data ready: org={ORG_ID} ({ORG_NAME}), owner={OWNER_ID}, clerkId={OWNER_CLERK_ID}")
    finally:
        conn.close()


if __name__ == "__main__":
    if "--reset" in sys.argv:
        reset()
    db.init_db()
    seed()
