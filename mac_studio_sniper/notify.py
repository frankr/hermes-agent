"""Alert delivery: Telegram, Twilio SMS, console.

Every stock hit alerts on every configured channel regardless of what the
buyer does — the human with a phone is the fallback purchase mechanism.
Channels are configured purely by environment variables so no secrets ever
live in the repo:

  SNIPER_TELEGRAM_BOT_TOKEN / SNIPER_TELEGRAM_CHAT_ID
  SNIPER_TWILIO_ACCOUNT_SID / SNIPER_TWILIO_AUTH_TOKEN
  SNIPER_TWILIO_FROM / SNIPER_TWILIO_TO
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .models import MatchResult

logger = logging.getLogger("mac_studio_sniper.notify")


class Notifier:
    def __init__(self, env: Optional[dict[str, str]] = None) -> None:
        e = env if env is not None else dict(os.environ)
        self.telegram_token = e.get("SNIPER_TELEGRAM_BOT_TOKEN")
        self.telegram_chat = e.get("SNIPER_TELEGRAM_CHAT_ID")
        self.twilio_sid = e.get("SNIPER_TWILIO_ACCOUNT_SID")
        self.twilio_token = e.get("SNIPER_TWILIO_AUTH_TOKEN")
        self.twilio_from = e.get("SNIPER_TWILIO_FROM")
        self.twilio_to = e.get("SNIPER_TWILIO_TO")

    def channels(self) -> list[str]:
        out = ["console"]
        if self.telegram_token and self.telegram_chat:
            out.append("telegram")
        if all((self.twilio_sid, self.twilio_token, self.twilio_from, self.twilio_to)):
            out.append("twilio")
        return out

    # -- delivery -----------------------------------------------------------

    def send_match_alert(self, match: MatchResult) -> list[str]:
        text = self._format_match(match)
        return self.send_raw(text)

    def send_raw(self, text: str) -> list[str]:
        """Send to every configured channel; returns channels that succeeded."""
        delivered = []
        print(text, flush=True)
        delivered.append("console")
        if "telegram" in self.channels() and self._send_telegram(text):
            delivered.append("telegram")
        if "twilio" in self.channels() and self._send_twilio_sms(text):
            delivered.append("twilio")
        return delivered

    @staticmethod
    def _format_match(match: MatchResult) -> str:
        lines = [
            "🚨 REFURB HIT — " + match.headline(),
            match.tile.product_url,
        ]
        if match.needs_verification:
            lines.append("⚠️ Specs not fully confirmed from tile data — verify RAM/price at the link.")
        if match.max_price_usd is not None:
            lines.append(f"Cap: ${match.max_price_usd:,.0f}")
        return "\n".join(lines)

    def _send_telegram(self, text: str) -> bool:
        import httpx

        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": self.telegram_chat, "text": text, "disable_web_page_preview": False},
                timeout=10,
            )
            ok = resp.status_code == 200
            if not ok:
                logger.error("telegram send failed: %s %s", resp.status_code, resp.text[:200])
            return ok
        except Exception:
            logger.exception("telegram send raised")
            return False

    def _send_twilio_sms(self, text: str) -> bool:
        import httpx

        try:
            resp = httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_sid}/Messages.json",
                data={"From": self.twilio_from, "To": self.twilio_to, "Body": text[:1500]},
                auth=(self.twilio_sid or "", self.twilio_token or ""),
                timeout=10,
            )
            ok = resp.status_code in (200, 201)
            if not ok:
                logger.error("twilio send failed: %s %s", resp.status_code, resp.text[:200])
            return ok
        except Exception:
            logger.exception("twilio send raised")
            return False
