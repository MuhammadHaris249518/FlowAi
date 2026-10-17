"""Tells the owner about handoffs and safety alerts, over every configured channel.

Channels are pluggable: each one says whether it is configured (env vars) and how to
send. The owner console (/owner) always works because it reads the escalations table
directly; channels here only *push* a notification.

Adding a channel (SMS, Slack...; Telegram is in telegram_bot.py, email in email_notify.py):
    1. Subclass Channel, implement enabled() / send_escalation() / send_alert().
    2. Add an instance to CHANNELS.
    3. If the channel can answer (e.g. Telegram buttons), call
       handoff.answer_escalation(escalation_id, choice=..., text=...) from its webhook.
A failing channel never breaks a call: errors are logged and the next channel runs.
"""

import logging
import threading
import os

log = logging.getLogger("talkse.notifier")


class Channel:
    name = "base"

    def enabled(self) -> bool:
        return False

    def send_escalation(self, escalation: dict, owner_console_url: str) -> None:
        """A pending handoff: the owner must approve / answer. Options are in escalation['options_json']."""
        raise NotImplementedError

    def send_alert(self, text: str) -> None:
        """A one-way urgent alert (safety hazard)."""
        raise NotImplementedError


class ConsoleChannel(Channel):
    """Always on: writes to the server log (visible in the Alternate Clouds Logs tab)."""
    name = "console"

    def enabled(self) -> bool:
        return True

    def send_escalation(self, escalation: dict, owner_console_url: str) -> None:
        log.warning("OWNER HANDOFF [%s] %s -> answer at %s", escalation["kind"], escalation["summary"], owner_console_url)

    def send_alert(self, text: str) -> None:
        log.warning("OWNER ALERT %s", text)


class TelegramChannel(Channel):
    """Owner's phone via the Telegram bot (telegram_bot.py). On when TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID are set."""
    name = "telegram"

    def enabled(self) -> bool:
        return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_OWNER_CHAT_ID"))

    def send_escalation(self, escalation: dict, owner_console_url: str) -> None:
        import telegram_bot  # imported here: telegram_bot imports handoff, which imports this module
        telegram_bot.send_escalation(escalation)

    def send_alert(self, text: str) -> None:
        import telegram_bot
        telegram_bot.send_alert(text)


class EmailChannel(Channel):
    """Owner's inbox via Resend (email_notify.py): approval buttons are signed one-click links.
    On when RESEND_API_KEY and OWNER_EMAIL are set."""
    name = "email"

    def enabled(self) -> bool:
        import email_notify
        return email_notify.enabled()

    def send_escalation(self, escalation: dict, owner_console_url: str) -> None:
        import email_notify
        email_notify.send_escalation(escalation)

    def send_alert(self, text: str) -> None:
        import email_notify
        email_notify.send_alert(text)


class WhatsAppChannel(Channel):
    """Owner's WhatsApp via Meta's Cloud API (whatsapp_notify.py): reply buttons for approvals.
    On when WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID and OWNER_WHATSAPP are set."""
    name = "whatsapp"

    def enabled(self) -> bool:
        import whatsapp_notify
        return whatsapp_notify.enabled()

    def send_escalation(self, escalation: dict, owner_console_url: str) -> None:
        import whatsapp_notify
        whatsapp_notify.send_escalation(escalation)

    def send_alert(self, text: str) -> None:
        import whatsapp_notify
        whatsapp_notify.send_alert(text)


class SmsChannel(Channel):
    """Slot for SMS (e.g. Twilio). On when SMS_PROVIDER_KEY and OWNER_PHONE are set."""
    name = "sms"

    def enabled(self) -> bool:
        return bool(os.getenv("SMS_PROVIDER_KEY") and os.getenv("OWNER_PHONE"))

    def send_escalation(self, escalation: dict, owner_console_url: str) -> None:
        log.info("SMS channel not implemented yet; escalation %s", escalation["id"])

    def send_alert(self, text: str) -> None:
        log.info("SMS channel not implemented yet; alert: %s", text)


CHANNELS: list[Channel] = [ConsoleChannel(), EmailChannel(), WhatsAppChannel(), TelegramChannel(), SmsChannel()]


def _owner_console_url() -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/owner" if base else "/owner"


def _run_in_background(fn, *args) -> None:
    """Network channels can be slow; the caller's turn must never wait on them."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _send_escalation(channel: Channel, escalation: dict) -> None:
    try:
        channel.send_escalation(escalation, _owner_console_url())
    except Exception as e:
        log.error("Channel %s failed for escalation %s: %s", channel.name, escalation.get("id"), e)


def _send_alert(channel: Channel, text: str) -> None:
    try:
        channel.send_alert(text)
    except Exception as e:
        log.error("Channel %s failed to send alert: %s", channel.name, e)


def notify_escalation(escalation: dict) -> None:
    for channel in CHANNELS:
        if channel.enabled():
            _run_in_background(_send_escalation, channel, escalation)


def alert_owner(text: str) -> None:
    for channel in CHANNELS:
        if channel.enabled():
            _run_in_background(_send_alert, channel, text)


def active_channels() -> list[str]:
    return [c.name for c in CHANNELS if c.enabled()]
