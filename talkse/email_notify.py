"""Owner notifications by email through Resend (https://resend.com), the alternative to Telegram.

Approvals arrive as an email with one button per option. Each button is a signed one-click
link to GET /owner/act?id=<escalation>&choice=<option>&sig=<hmac>, so the owner decides from
their phone's mail app and the AI tells the waiting caller. Questions link to the owner
console, where the owner types the answer.

Env:
    RESEND_API_KEY      required
    OWNER_EMAIL         required: where alerts go (on Resend's free tier: your sign-up email)
    RESEND_FROM_EMAIL   optional, default "Talkse <onboarding@resend.dev>" (needs no domain)
    ACTION_LINK_SECRET  optional HMAC key for the links (falls back to RESEND_API_KEY)
"""

import hashlib
import hmac
import json
import os
import urllib.request
from html import escape
from urllib.parse import urlencode

import clinic_config as config

RESEND_URL = "https://api.resend.com/emails"
BUTTON_LABELS = {"approve": "Approve", "site_visit": "Book a site visit", "callback": "I'll call them back"}


def enabled() -> bool:
    return bool(os.getenv("RESEND_API_KEY") and os.getenv("OWNER_EMAIL"))


def _secret() -> bytes:
    return (os.getenv("ACTION_LINK_SECRET") or os.getenv("RESEND_API_KEY") or "").encode()


def sign(escalation_id: str, choice: str) -> str:
    return hmac.new(_secret(), f"{escalation_id}:{choice}".encode(), hashlib.sha256).hexdigest()[:32]


def verify(escalation_id: str, choice: str, sig: str) -> bool:
    return bool(_secret()) and hmac.compare_digest(sign(escalation_id, choice), sig or "")


def _base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


def _owner_console_url() -> str:
    key = os.getenv("OWNER_CONSOLE_KEY")
    return f"{_base_url()}/owner" + (f"?key={key}" if key else "")


def _send(subject: str, html: str) -> None:
    body = json.dumps({
        "from": os.getenv("RESEND_FROM_EMAIL") or "Talkse <onboarding@resend.dev>",
        "to": [os.environ["OWNER_EMAIL"]],
        "subject": subject,
        "html": html,
    }).encode()
    req = urllib.request.Request(RESEND_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
        "Content-Type": "application/json",
        "User-Agent": "talkse/1.0",
    })
    with urllib.request.urlopen(req, timeout=15) as res:
        res.read()


def _button(href: str, label: str, primary: bool) -> str:
    style = ("background:#0d9488;color:#fff;" if primary else "background:#fff;color:#0f2440;border:1px solid #cbd5e1;")
    return (f'<a href="{escape(href)}" style="{style}display:inline-block;padding:12px 18px;margin:4px 6px 4px 0;'
            f'border-radius:10px;font-weight:600;text-decoration:none;font-family:Arial,sans-serif">{escape(label)}</a>')


def _layout(title: str, body_html: str) -> str:
    return (f'<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#0f2440">'
            f'<p style="color:#0d9488;font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase">'
            f'{escape(config.BUSINESS_NAME)} · AI receptionist</p>'
            f'<h2 style="margin:0 0 12px">{escape(title)}</h2>{body_html}'
            f'<p style="color:#94a3b8;font-size:12px;margin-top:24px">Sent by Talkse. '
            f'<a href="{escape(_owner_console_url())}" style="color:#94a3b8">Open the owner console</a></p></div>')


def send_escalation(escalation: dict) -> None:
    """A caller is on hold: approval buttons (signed links) or a link to answer the question."""
    details = escalation.get("options_json") or {}
    options = details.get("options", []) if isinstance(details, dict) else []
    timeout = os.getenv("HANDOFF_TIMEOUT_SECONDS", "45")
    summary = "<br>".join(escape(part) for part in escalation["summary"].split(" | "))
    box = f'<div style="background:#f1f5f9;border-radius:12px;padding:14px 16px;line-height:1.5">{summary}</div>'

    if escalation["kind"] == "approval" and options:
        links = "".join(
            _button(f"{_base_url()}/owner/act?"
                    + urlencode({"id": escalation["id"], "choice": o["id"], "sig": sign(str(escalation["id"]), o["id"])}),
                    BUTTON_LABELS.get(o["id"], o["label"]), i == 0)
            for i, o in enumerate(options))
        title, subject = "A caller needs your approval", f"Approve? Caller on hold ({timeout}s)"
        html = _layout(title, f"{box}<p>The caller is on hold for up to {timeout} seconds. Tap one:</p>{links}")
    else:
        title, subject = "A caller asked something new", f"Caller question, on hold ({timeout}s)"
        html = _layout(title, f"{box}<p>Type your answer in the owner console; the AI says it to the caller "
                              f"(and can remember it for next time).</p>"
                              f"{_button(_owner_console_url(), 'Answer now', True)}")
    _send(subject, html)


def send_alert(text: str) -> None:
    _send(f"SAFETY ALERT · {config.BUSINESS_NAME}",
          _layout("Safety alert from a caller",
                  f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:14px 16px;'
                  f'color:#7f1d1d;line-height:1.5">{escape(text)}</div>'
                  f"<p>The caller was told to leave the house and call 911. Please call them back.</p>"))
