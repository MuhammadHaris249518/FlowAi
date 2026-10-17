"""Owner notifications on WhatsApp through Meta's WhatsApp Cloud API (free test number works).

Approvals arrive with up to 3 reply buttons (Approve / Site visit / Call them back); tapping one
reaches POST /whatsapp/webhook and is applied like a Telegram button. A typed reply answers the
newest pending caller question. Only messages from OWNER_WHATSAPP are accepted.

Free use: inside the 24 h window after the owner last messaged the business number, free-form
messages with buttons cost nothing. Outside it Meta rejects them (error 131047): send "hi" to
the number to reopen the window. Email keeps working as the other channel either way.

Env:
    WHATSAPP_TOKEN            access token (API Setup page; temporary tokens expire after 24 h)
    WHATSAPP_PHONE_NUMBER_ID  the sending number's ID (API Setup page)
    OWNER_WHATSAPP            owner's number, digits with country code, e.g. 923001234567
    WHATSAPP_VERIFY_TOKEN     any string; typed into Meta's webhook settings to verify the URL
    WHATSAPP_APP_SECRET       optional: checks Meta's X-Hub-Signature-256 on incoming webhooks
"""

import hashlib
import hmac
import json
import logging
import os
import re
import urllib.error
import urllib.request

import clinic_config as config
import db
import handoff

log = logging.getLogger("talkse.whatsapp")

GRAPH_URL = "https://graph.facebook.com/v21.0"
BUTTON_TITLES = {"approve": "Approve", "site_visit": "Site visit", "callback": "Call them back"}  # max 20 chars


def enabled() -> bool:
    return bool(os.getenv("WHATSAPP_TOKEN") and os.getenv("WHATSAPP_PHONE_NUMBER_ID") and os.getenv("OWNER_WHATSAPP"))


def _owner() -> str:
    return re.sub(r"\D", "", os.getenv("OWNER_WHATSAPP", ""))


def _post(payload: dict) -> dict:
    req = urllib.request.Request(
        f"{GRAPH_URL}/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages",
        data=json.dumps({"messaging_product": "whatsapp", "to": _owner(), **payload}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {os.environ['WHATSAPP_TOKEN']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if "131047" in detail:
            log.warning("WhatsApp: outside the 24 h window. Owner must message the business number first.")
        raise RuntimeError(f"WhatsApp send failed: HTTP {e.code} {detail}") from None


def send_text(text: str) -> None:
    _post({"type": "text", "text": {"body": text[:4096]}})


def send_escalation(escalation: dict) -> None:
    details = escalation.get("options_json") or {}
    options = details.get("options", []) if isinstance(details, dict) else []
    timeout = os.getenv("HANDOFF_TIMEOUT_SECONDS", "45")
    body = escalation["summary"].replace(" | ", "\n")

    if escalation["kind"] == "approval" and options:
        _post({"type": "interactive", "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "Caller on hold: approve?"},
            "body": {"text": f"{body}\n\nThe caller waits up to {timeout}s."[:1024]},
            "footer": {"text": config.BUSINESS_NAME[:60]},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": f"esc:{escalation['id']}:{o['id']}",
                                            "title": BUTTON_TITLES.get(o["id"], o["label"])[:20]}}
                for o in options[:3]
            ]},
        }})
    else:
        send_text(f"Caller question (on hold {timeout}s):\n\n{body}\n\nReply to this chat with your answer; "
                  f"the AI says it to the caller.")


def send_alert(text: str) -> None:
    send_text(f"SAFETY ALERT ({config.BUSINESS_NAME})\n\n{text}\n\nThe caller was told to leave and call 911.")


# ---- Incoming (webhook) ---------------------------------------------------------

def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    """X-Hub-Signature-256 check, only when WHATSAPP_APP_SECRET is set."""
    secret = os.getenv("WHATSAPP_APP_SECRET")
    if not secret:
        return True
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def _latest_pending_question() -> dict | None:
    for esc in db.list_escalations(30):
        if esc["kind"] == "question" and esc["status"] == "pending":
            return esc
    return None


def handle_webhook(payload: dict) -> None:
    """Applies the owner's button taps and typed answers. Ignores everyone except OWNER_WHATSAPP."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                if re.sub(r"\D", "", msg.get("from", "")) != _owner():
                    log.info("WhatsApp: ignored message from a non-owner number")
                    continue
                try:
                    _handle_owner_message(msg)
                except Exception as e:
                    log.error("WhatsApp message %s failed: %s", msg.get("id"), e)


def _handle_owner_message(msg: dict) -> None:
    if msg.get("type") == "interactive":
        reply_id = (msg.get("interactive", {}).get("button_reply") or {}).get("id", "")
        parts = reply_id.split(":")
        if len(parts) == 3 and parts[0] == "esc":
            result = handoff.answer_escalation(parts[1], choice=parts[2])
            send_text(f"Done: {BUTTON_TITLES.get(parts[2], parts[2])}. The AI is telling the caller now."
                      if result["ok"] else f"Not applied: {result.get('error')}")
        return

    if msg.get("type") == "text":
        text = msg.get("text", {}).get("body", "").strip()
        esc = _latest_pending_question()
        if not esc:
            if text.lower() not in ("hi", "hello", "start"):
                send_text("No caller is waiting for an answer right now.")
            else:
                send_text(f"Hi {config.OWNER_NAME}! Caller alerts for {config.BUSINESS_NAME} will arrive here.")
            return
        result = handoff.answer_escalation(str(esc["id"]), text=text)
        send_text("Sent to the caller." if result["ok"] else f"Not applied: {result.get('error')}")
