"""Seeds FlowCRM (Prisma tables in the "public" schema) with demo data for Cool Breeze AC Services.

Run once after `npx prisma db push` has created FlowCRM's tables:
    python seed_crm.py

IDs are fixed so the env vars are known in advance:
    CRM_ORG_ID=org_coolbreeze  CRM_OWNER_USER_ID=usr_ahmed  DEMO_CLERK_ID=demo_ahmed
Safe to re-run: every insert is ON CONFLICT DO NOTHING.
"""

from datetime import datetime, timedelta, timezone

import db

ORG_ID = "org_coolbreeze"
OWNER_ID = "usr_ahmed"
OWNER_CLERK_ID = "demo_ahmed"

CONTACTS = [
    ("ct_01", "Ayesha Malik", "0300-1234567", "ayesha.malik@example.com"),
    ("ct_02", "Bilal Ahmed", "0301-2345678", "bilal.ahmed@example.com"),
    ("ct_03", "Fatima Noor", "0321-3456789", "fatima.noor@example.com"),
    ("ct_04", "Hamza Qureshi", "0333-4567890", "hamza.q@example.com"),
    ("ct_05", "Sana Javed", "0345-5678901", "sana.javed@example.com"),
    ("ct_06", "Usman Tariq", "0312-6789012", "usman.tariq@example.com"),
    ("ct_07", "Zainab Raza", "0302-7890123", "zainab.raza@example.com"),
    ("ct_08", "Imran Siddiqui", "0335-8901234", "imran.s@example.com"),
]

# (id, contact, status, source, score, notes)
LEADS = [
    ("ld_01", "ct_01", "NEW", "Phone call", 55, "AC not cooling, F-10. Wants a visit this week."),
    ("ld_02", "ct_02", "CONTACTED", "Website form", 62, "Gas refill for 2 split units, G-11."),
    ("ld_03", "ct_03", "QUALIFIED", "Referral", 80, "New 1.5 ton inverter AC installation, E-11."),
    ("ld_04", "ct_04", "NEW", "Phone call", 40, "Asked about service charges and warranty."),
    ("ld_05", "ct_05", "QUALIFIED", "Walk-in", 75, "Office: 4 ACs annual service contract, Blue Area."),
    ("ld_06", "ct_06", "CONTACTED", "Facebook", 50, "Geyser not heating, I-8."),
    ("ld_07", "ct_07", "DISQUALIFIED", "Phone call", 15, "Outside service zone (Rawat)."),
    ("ld_08", "ct_08", "CONVERTED", "Referral", 90, "Plumbing leak fixed; repeat customer."),
]

# (id, contact, title, value PKR, stage, days ago closed or None)
DEALS = [
    ("dl_01", "ct_01", "AC repair · Ayesha · F-10", 4500, "MEETING", None),
    ("dl_02", "ct_02", "Gas refill x2 · Bilal · G-11", 12000, "PROPOSAL", None),
    ("dl_03", "ct_03", "Inverter AC installation · Fatima · E-11", 18500, "NEGOTIATION", None),
    ("dl_04", "ct_05", "Annual service contract · 4 ACs · Blue Area", 48000, "QUALIFIED", None),
    ("dl_05", "ct_08", "Plumbing leak repair · Imran", 6000, "WON", 3),
    ("dl_06", "ct_07", "AC service · Zainab · Rawat", 3500, "LOST", 5),
]

# (id, title, priority, contact, lead, due in hours)
TASKS = [
    ("tk_01", "Call back Ayesha to confirm Thursday visit", "HIGH", "ct_01", "ld_01", 3),
    ("tk_02", "Send installation quote to Fatima", "MEDIUM", "ct_03", "ld_03", 24),
    ("tk_03", "Schedule site survey for Blue Area office", "MEDIUM", "ct_05", "ld_05", 48),
    ("tk_04", "Order gas cylinders for weekend refills", "LOW", None, None, 72),
]

ACTIVITIES = [
    ("CALL_LOGGED", "Call logged with Ayesha Malik (AC not cooling)", 2),
    ("LEAD_CREATED", "New lead from website form: Bilal Ahmed", 6),
    ("DEAL_STAGE_CHANGED", "Inverter AC installation moved to Negotiation", 20),
    ("MEETING_SCHEDULED", "Site visit scheduled with Fatima Noor", 26),
]


def seed() -> None:
    now = datetime.now(timezone.utc)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO "Organization" (id, name, slug, "updatedAt")
                VALUES (%s, 'Cool Breeze AC Services', 'cool-breeze', now())
                ON CONFLICT DO NOTHING;
            """, (ORG_ID,))
            cur.execute("""
                INSERT INTO "User" (id, "clerkId", email, "fullName")
                VALUES (%s, %s, 'ahmed@coolbreeze.example', 'Ahmed (Owner)')
                ON CONFLICT DO NOTHING;
            """, (OWNER_ID, OWNER_CLERK_ID))
            cur.execute("""
                INSERT INTO "Membership" (id, role, "userId", "organizationId")
                VALUES ('mb_ahmed', 'ORG_OWNER', %s, %s)
                ON CONFLICT DO NOTHING;
            """, (OWNER_ID, ORG_ID))

            for cid, name, phone, email in CONTACTS:
                cur.execute("""
                    INSERT INTO "Contact" (id, "fullName", phone, email, "organizationId", "ownerId", "updatedAt")
                    VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT DO NOTHING;
                """, (cid, name, phone, email, ORG_ID, OWNER_ID))

            for lid, cid, status, source, score, notes in LEADS:
                cur.execute("""
                    INSERT INTO "Lead" (id, status, source, notes, score, "organizationId", "contactId", "assigneeId", "updatedAt")
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT DO NOTHING;
                """, (lid, status, source, notes, score, ORG_ID, cid, OWNER_ID))

            for did, cid, title, value, stage, closed_days in DEALS:
                closed_at = now - timedelta(days=closed_days) if closed_days else None
                lost_reason = "BUDGET" if stage == "LOST" else None
                cur.execute("""
                    INSERT INTO "Deal" (id, title, value, stage, "organizationId", "contactId", "assigneeId", "closedAt", "lostReason", "updatedAt")
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT DO NOTHING;
                """, (did, title, value, stage, ORG_ID, cid, OWNER_ID, closed_at, lost_reason))

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
        conn.commit()
        print(f"[Seed] FlowCRM demo data ready: org={ORG_ID}, owner={OWNER_ID}, clerkId={OWNER_CLERK_ID}")
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
