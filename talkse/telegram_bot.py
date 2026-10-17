"""Telegram channel for the owner: handoff requests with buttons, typed answers, safety alerts.

    Haris's phone:  "🔔 Caller on hold..."  [✅ Approve] [📋 Free estimate first] [📞 I'll call them]
    tap a button   -> handoff.answer_escalation(choice=...) -> the AI tells the caller
    reply by text  -> handoff.answer_escalation(text=...)   -> the AI speaks it, then [💾 Save as FAQ]

Updates arrive by long polling (TELEGRAM_MODE=polling, the default): no public URL or
webhook setup needed, works locally and on Alternate Clouds. TELEGRAM_MODE=webhook
uses POST /telegram/webhook instead; TELEGRAM_MODE=off disables receiving.
Only one poller per bot can run: don't poll from a laptop and the deployed server at once.

Only messages from TELEGRAM_OWNER_CHAT_ID are accepted. Sending /start to the bot
replies with your chat ID, to fill in that variable.
"""

import json
import logging
import os
import threading
import urllib.request

import clinic_config as config
import db
import handoff

log = logging.getLogger("talkse.telegram")

BUTTON_LABELS = {"approve": "✅ Approve, book it", "site_visit": "📋 Free estimate first", "callback": "📞 I'll call them"}
OUTCOMES = {"approve": "✅ Approved", "site_visit": "📋 Free estimate visit chosen", "callback": "📞 You'll call them back"}


def token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def owner_chat() -> str:
    return os.getenv("TELEGRAM_OWNER_CHAT_ID", "")


def mode() -> str:
    return os.getenv("TELEGRAM_MODE", "polling").lower()


def call(method: str, payload: dict | None = None, timeout: int = 15):
    """Calls the Telegram Bot API and returns its result; raises on errors."""
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token()}/{method}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = json.loads(res.read())
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
    return body["result"]


def _send(chat_id: str, text: str, buttons: list[list[dict]] | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return call("sendMessage", payload)


# ---- Outgoing ------------------------------------------------------------------

def send_escalation(escalation: dict) -> None:
    """Pushes a pending handoff to the owner's phone with one button per option."""
    details = escalation.get("options_json") or {}
    options = details.get("options", []) if isinstance(details, dict) else []
    timeout = os.getenv("HANDOFF_TIMEOUT_SECONDS", "45")
    if escalation["kind"] == "approval":
        head = f"🔔 Caller on hold: needs your approval ({timeout}s)"
        tail = ""
    else:
        head = f"❓ Caller question ({timeout}s)"
        tail = "\n\n↩️ Reply to this message with your answer. The AI will say it to the caller."
    body = escalation["summary"].replace(" | ", "\n")
    buttons = [[{"text": BUTTON_LABELS.get(o["id"], o["label"]),
                 "callback_data": f"esc:{escalation['id']}:{o['id']}"}] for o in options]
    msg = _send(owner_chat(), f"{head}\n\n{body}{tail}", buttons)
    db.set_escalation_telegram_message(escalation["id"], msg["message_id"])


def send_alert(text: str) -> None:
    _send(owner_chat(), f"⚠️ SAFETY ALERT ({config.BUSINESS_NAME})\n\n{text}")


# ---- Incoming ------------------------------------------------------------------

def _close_message(message: dict, outcome: str) -> None:
    """Rewrites the request message with the outcome and removes its buttons."""
    try:
        call("editMessageText", {"chat_id": message["chat"]["id"], "message_id": message["message_id"],
                                 "text": f"{message.get('text', '')}\n\n{outcome}"})
    except Exception as e:
        log.info("Could not edit Telegram message: %s", e)


def _handle_callback(cq: dict) -> None:
    message = cq.get("message") or {}
    if str(message.get("chat", {}).get("id")) != owner_chat():
        call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Not authorized"})
        return

    data = cq.get("data", "")
    if data.startswith("esc:"):
        _, esc_id, choice = data.split(":", 2)
        result = handoff.answer_escalation(esc_id, choice=choice)
        call("answerCallbackQuery", {"callback_query_id": cq["id"],
                                     "text": "Sent to the caller" if result["ok"] else result["error"]})
        if result["ok"]:
            _close_message(message, OUTCOMES.get(choice, "✔ Done"))
    elif data.startswith("faq:"):
        result = handoff.save_answer_as_faq(data.split(":", 1)[1])
        call("answerCallbackQuery", {"callback_query_id": cq["id"],
                                     "text": "Saved: the AI will answer this itself next time" if result["ok"]
                                     else result["error"]})
        if result["ok"]:
            _close_message(message, "💾 Saved as FAQ")


def _handle_message(msg: dict) -> None:
    chat_id = str(msg.get("chat", {}).get("id"))
    text = (msg.get("text") or "").strip()

    if text.startswith("/start"):
        if chat_id == owner_chat():
            _send(chat_id, f"You're connected as the owner of {config.BUSINESS_NAME}. "
                           "Caller handoffs and safety alerts will arrive here.")
        else:
            _send(chat_id, f"Hi! Your chat ID is {chat_id}.\nSet TELEGRAM_OWNER_CHAT_ID={chat_id} "
                           "on the Talkse server to receive caller handoffs here.")
        return
    if chat_id != owner_chat() or not text:
        return

    replied_to = msg.get("reply_to_message")
    esc = db.get_escalation_by_telegram_message(replied_to["message_id"]) if replied_to else None
    if not esc or esc["kind"] != "question":
        _send(chat_id, "To answer a caller, swipe to reply to their question message.")
        return

    result = handoff.answer_escalation(str(esc["id"]), text=text)
    if not result["ok"]:
        _send(chat_id, f"Couldn't send: {result['error']}")
        return
    _close_message(replied_to, f"✔ You answered: {text}")
    _send(chat_id, "✔ Sent to the caller. Save it so the AI answers this question itself next time?",
          [[{"text": "💾 Save as FAQ", "callback_data": f"faq:{esc['id']}"}]])


def handle_update(update: dict) -> None:
    """Processes one Telegram update (from polling or the webhook)."""
    if update.get("callback_query"):
        _handle_callback(update["callback_query"])
    elif update.get("message"):
        _handle_message(update["message"])


# ---- Polling -------------------------------------------------------------------

def _poll_loop(stop: threading.Event) -> None:
    try:
        call("deleteWebhook")  # getUpdates is refused while a webhook is set
    except Exception as e:
        log.info("deleteWebhook: %s", e)
    offset = None
    conflict_logged = False
    log.info("Telegram polling started")
    while not stop.is_set():
        try:
            updates = call("getUpdates", {"timeout": 25, "offset": offset,
                                          "allowed_updates": ["message", "callback_query"]}, timeout=35)
            conflict_logged = False
        except Exception as e:
            # 409: another process polls the same bot token (an old deployment still running,
            # a second replica, or a local run). Say so once, back off, take over when it stops.
            if "409" in str(e):
                if not conflict_logged:
                    log.warning("Telegram 409 Conflict: another Talkse instance is polling this bot token. "
                                "Stop the other instance (old deployment / local run); retrying every 30 s.")
                    conflict_logged = True
                stop.wait(30)
            else:
                log.warning("Telegram polling error: %s", e)
                stop.wait(5)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            try:
                handle_update(update)
            except Exception as e:
                log.error("Telegram update %s failed: %s", update.get("update_id"), e)


def start_polling() -> threading.Event | None:
    """Starts the background poller if a token is set and TELEGRAM_MODE=polling. Returns its stop event."""
    if not token() or mode() != "polling":
        return None
    stop = threading.Event()
    threading.Thread(target=_poll_loop, args=(stop,), daemon=True, name="telegram-poller").start()
    return stop
