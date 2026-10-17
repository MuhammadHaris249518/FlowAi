import os
import json
import time
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB_URL = "postgresql://postgres:postgres@localhost:5433/postgres"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB_URL)

def get_connection():
    # One retry: Neon can drop the first connection while its compute wakes up from idle
    try:
        return psycopg2.connect(DATABASE_URL, connect_timeout=10)
    except psycopg2.OperationalError as e:
        print(f"[DB] Connection failed ({e}); retrying once...")
        time.sleep(1)
        return psycopg2.connect(DATABASE_URL, connect_timeout=10)

def init_db():
    """Initializes the appointments table and unique slot index in Postgres."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Enable pgcrypto extension if needed for gen_random_uuid
            cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            # Talkse lives in its own schema so FlowCRM's `prisma db push` on "public" never touches it
            cur.execute("CREATE SCHEMA IF NOT EXISTS talkse;")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS talkse.appointments (
                    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    idempotency_key    TEXT UNIQUE NOT NULL,
                    service_id         TEXT NOT NULL,
                    provider_id        TEXT NOT NULL,
                    caller_name        TEXT NOT NULL,
                    caller_phone       TEXT,
                    scheduled_start    TIMESTAMPTZ NOT NULL,
                    scheduled_end      TIMESTAMPTZ NOT NULL,
                    status             TEXT NOT NULL DEFAULT 'confirmed',
                    deposit_required   BOOLEAN NOT NULL DEFAULT false,
                    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            
            # Known Limitation / Future Schema Enhancement:
            # uq_provider_slot prevents two confirmed bookings at the exact same scheduled_start for a provider.
            # Intersecting/overlapping time windows (e.g. 2:00-2:30 vs 2:15-2:45) are caught by get_overlapping_appointments()
            # in application logic. A strict DB-level exclusion constraint for overlapping time ranges would require
            # CREATE EXTENSION btree_gist; EXCLUDE USING gist (provider_id WITH =, tsrange(scheduled_start, scheduled_end) WITH &&).
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_slot
                    ON talkse.appointments (provider_id, scheduled_start)
                    WHERE status = 'confirmed';
            """)

            # One row per browser call; state_json holds the conversation state so a
            # container restart mid-call does not lose it
            cur.execute("""
                CREATE TABLE IF NOT EXISTS talkse.calls (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    caller_phone    TEXT,
                    caller_name     TEXT,
                    state_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
                    status          TEXT NOT NULL DEFAULT 'collecting',
                    transcript      JSONB NOT NULL DEFAULT '[]'::jsonb,
                    crm_contact_id  TEXT,
                    crm_lead_id     TEXT,
                    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    ended_at        TIMESTAMPTZ
                );
            """)

            # Human handoffs (Phase 7): owner approvals, unknown questions, hazards
            cur.execute("""
                CREATE TABLE IF NOT EXISTS talkse.escalations (
                    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    call_id              UUID REFERENCES talkse.calls(id),
                    kind                 TEXT NOT NULL,
                    summary              TEXT,
                    options_json         JSONB NOT NULL DEFAULT '[]'::jsonb,
                    status               TEXT NOT NULL DEFAULT 'pending',
                    answer               TEXT,
                    telegram_message_id  BIGINT,
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
                    answered_at          TIMESTAMPTZ
                );
            """)

            # FAQs the AI can answer without a human; source = 'seed' or 'staff' (Phase 8)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS talkse.faqs (
                    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    question       TEXT NOT NULL,
                    variants_json  JSONB NOT NULL DEFAULT '[]'::jsonb,
                    answer         TEXT NOT NULL,
                    source         TEXT NOT NULL DEFAULT 'seed',
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            conn.commit()
            print("[DB] Initialized talkse schema (appointments, calls, escalations, faqs).")
    finally:
        conn.close()

def get_appointment(id_or_key: str) -> dict | None:
    """Fetches an appointment by ID or idempotency_key."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM talkse.appointments 
                WHERE id::text = %s OR idempotency_key = %s;
            """, (str(id_or_key), str(id_or_key)))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()

def get_overlapping_appointments(provider_id: str, start_dt, end_dt, exclude_id: str = None) -> list[dict]:
    """Finds confirmed appointments overlapping [start_dt, end_dt) for a provider."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if exclude_id:
                cur.execute("""
                    SELECT * FROM talkse.appointments
                    WHERE provider_id = %s
                      AND status = 'confirmed'
                      AND id::text != %s
                      AND (scheduled_start < %s AND scheduled_end > %s);
                """, (provider_id, str(exclude_id), end_dt, start_dt))
            else:
                cur.execute("""
                    SELECT * FROM talkse.appointments
                    WHERE provider_id = %s
                      AND status = 'confirmed'
                      AND (scheduled_start < %s AND scheduled_end > %s);
                """, (provider_id, end_dt, start_dt))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def insert_appointment(idempotency_key: str, service_id: str, provider_id: str, caller_name: str, scheduled_start, scheduled_end, deposit_required: bool = False, caller_phone: str = None) -> dict:
    """Inserts a new appointment row or returns existing row if idempotency_key exists."""
    existing = get_appointment(idempotency_key)
    if existing:
        print(f"[DB] Idempotency key '{idempotency_key}' found. Returning existing appointment.")
        return existing
        
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO talkse.appointments (idempotency_key, service_id, provider_id, caller_name, caller_phone, scheduled_start, scheduled_end, deposit_required)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *;
            """, (idempotency_key, service_id, provider_id, caller_name, caller_phone, scheduled_start, scheduled_end, deposit_required))
            row = cur.fetchone()
            conn.commit()
            return dict(row)
    finally:
        conn.close()

def cancel_appointment(id_or_key: str) -> dict | None:
    """Marks an appointment status as cancelled."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE talkse.appointments
                SET status = 'cancelled', updated_at = now()
                WHERE id::text = %s OR idempotency_key = %s
                RETURNING *;
            """, (str(id_or_key), str(id_or_key)))
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
    finally:
        conn.close()

def update_appointment_time(id_or_key: str, new_start, new_end) -> dict | None:
    """Updates scheduled start and end time of an appointment."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE talkse.appointments
                SET scheduled_start = %s, scheduled_end = %s, updated_at = now()
                WHERE id::text = %s OR idempotency_key = %s
                RETURNING *;
            """, (new_start, new_end, str(id_or_key), str(id_or_key)))
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
    finally:
        conn.close()

def create_escalation(call_id: str, kind: str, summary: str, details: dict, status: str = "pending") -> dict:
    """Creates a handoff to the owner. kind: approval | question | hazard. details holds options and the raw question."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO talkse.escalations (call_id, kind, summary, options_json, status)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *;
            """, (str(call_id), kind, summary, json.dumps(details, default=str), status))
            row = cur.fetchone()
            conn.commit()
            return dict(row)
    finally:
        conn.close()

def get_escalation(escalation_id: str) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM talkse.escalations WHERE id::text = %s;", (str(escalation_id),))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()

def transition_escalation(escalation_id: str, from_status: str, to_status: str, answer: str = None) -> dict | None:
    """Moves an escalation from one status to another, only if it is still in from_status.

    Atomic, so two owner clicks, or the owner answering just as the timeout fires,
    can never both win. Returns the updated row, or None if someone else got there first.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE talkse.escalations
                SET status = %s,
                    answer = COALESCE(%s, answer),
                    answered_at = CASE WHEN %s = 'answered' THEN now() ELSE answered_at END
                WHERE id::text = %s AND status = %s
                RETURNING *;
            """, (to_status, answer, to_status, str(escalation_id), from_status))
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
    finally:
        conn.close()

def find_booking(caller_name: str, scheduled_start) -> dict | None:
    """A confirmed appointment for this caller at this exact time, if one exists."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM talkse.appointments
                WHERE status = 'confirmed' AND lower(caller_name) = lower(%s) AND scheduled_start = %s
                LIMIT 1;
            """, (caller_name, scheduled_start))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()

def list_calls(limit: int = 10) -> list[dict]:
    """Most recent calls, newest first."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM talkse.calls ORDER BY started_at DESC LIMIT %s;", (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def set_escalation_telegram_message(escalation_id: str, message_id: int) -> None:
    """Remembers which Telegram message carries this escalation, so a reply to it can be matched."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE talkse.escalations SET telegram_message_id = %s WHERE id::text = %s;",
                        (message_id, str(escalation_id)))
        conn.commit()
    finally:
        conn.close()

def get_escalation_by_telegram_message(message_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM talkse.escalations WHERE telegram_message_id = %s;", (message_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()

def list_escalations(limit: int = 30) -> list[dict]:
    """Recent escalations, newest first, with the caller's name, for the owner console."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT e.*, c.caller_name, c.status AS call_status
                FROM talkse.escalations e
                LEFT JOIN talkse.calls c ON c.id = e.call_id
                ORDER BY e.created_at DESC
                LIMIT %s;
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def insert_faq(question: str, answer: str, source: str = "staff") -> dict:
    """Saves an FAQ the AI can answer from now on (Phase 8 learning)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO talkse.faqs (question, answer, source)
                VALUES (%s, %s, %s)
                RETURNING *;
            """, (question, answer, source))
            row = cur.fetchone()
            conn.commit()
            return dict(row)
    finally:
        conn.close()

def get_faqs() -> list[dict]:
    """Staff-saved FAQs (talkse.faqs), oldest first."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, question, answer, source FROM talkse.faqs ORDER BY created_at;")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def create_call(state: dict, caller_phone: str = None) -> dict:
    """Creates a call row and returns it."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO talkse.calls (caller_phone, state_json, status)
                VALUES (%s, %s, %s)
                RETURNING *;
            """, (caller_phone, json.dumps(state, default=str), state.get("status", "collecting")))
            row = cur.fetchone()
            conn.commit()
            return dict(row)
    finally:
        conn.close()

def get_call(call_id: str) -> dict | None:
    """Fetches a call by ID."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM talkse.calls WHERE id::text = %s;", (str(call_id),))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()

def update_call(call_id: str, state: dict, new_turns: list[dict] = None) -> dict | None:
    """Saves the call state, appends transcript turns and sets ended_at once the call is over."""
    terminal = state.get("status") not in ("collecting", "waiting_human")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE talkse.calls
                SET state_json = %s,
                    status = %s,
                    caller_name = COALESCE(%s, caller_name),
                    transcript = transcript || %s::jsonb,
                    ended_at = CASE WHEN %s AND ended_at IS NULL THEN now() ELSE ended_at END
                WHERE id::text = %s
                RETURNING *;
            """, (
                json.dumps(state, default=str),
                state.get("status", "collecting"),
                state.get("caller_name"),
                json.dumps(new_turns or []),
                terminal,
                str(call_id),
            ))
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
