"""Human-in-the-loop broker: confirm-to-buy replies and 2FA code relay.

One mechanism serves both needs (gates 3.2 and 2.4): send a prompt, wait
for a reply matching a pattern within a timeout.

Channels:
  TelegramInteractor — sends via bot, polls getUpdates for a reply from
                       the configured chat. Transport is injectable for
                       tests.
  FileInteractor     — writes <state_dir>/ask/<name>.prompt and polls for
                       <name>.answer. Works headless/offline; also the
                       local fallback ("echo BUY > …/confirm.answer").
``build_interactor`` picks Telegram when configured, else the file channel.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("mac_studio_sniper.interact")


class Interactor:
    def ask(
        self, name: str, prompt: str, reply_pattern: str, timeout_s: float
    ) -> Optional[str]:  # pragma: no cover - interface
        raise NotImplementedError


class FileInteractor(Interactor):
    def __init__(self, state_dir: Path, poll_s: float = 0.25) -> None:
        self.ask_dir = state_dir / "ask"
        self.ask_dir.mkdir(parents=True, exist_ok=True)
        self.poll_s = poll_s

    def ask(self, name: str, prompt: str, reply_pattern: str, timeout_s: float) -> Optional[str]:
        prompt_file = self.ask_dir / f"{name}.prompt"
        answer_file = self.ask_dir / f"{name}.answer"
        answer_file.unlink(missing_ok=True)
        prompt_file.write_text(
            prompt + f"\n\n(answer by writing to: {answer_file})\n", encoding="utf-8"
        )
        deadline = time.monotonic() + timeout_s
        pattern = re.compile(reply_pattern)
        while time.monotonic() < deadline:
            if answer_file.exists():
                text = answer_file.read_text(encoding="utf-8").strip()
                answer_file.unlink(missing_ok=True)
                if pattern.match(text):
                    prompt_file.unlink(missing_ok=True)
                    return text
                logger.warning("file answer %r did not match %r", text, reply_pattern)
            time.sleep(self.poll_s)
        prompt_file.unlink(missing_ok=True)
        return None


class TelegramInteractor(Interactor):
    def __init__(
        self,
        token: str,
        chat_id: str,
        http_get: Optional[Callable[[str, dict], dict]] = None,
        http_post: Optional[Callable[[str, dict], dict]] = None,
        poll_s: float = 2.0,
    ) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.poll_s = poll_s
        self._get = http_get or self._default_get
        self._post = http_post or self._default_post

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def _default_get(self, method: str, params: dict) -> dict:
        import httpx

        return httpx.get(self._url(method), params=params, timeout=15).json()

    def _default_post(self, method: str, payload: dict) -> dict:
        import httpx

        return httpx.post(self._url(method), json=payload, timeout=15).json()

    def _latest_update_id(self) -> int:
        data = self._get("getUpdates", {"timeout": 0})
        ids = [u.get("update_id", 0) for u in data.get("result", [])]
        return max(ids) if ids else 0

    def ask(self, name: str, prompt: str, reply_pattern: str, timeout_s: float) -> Optional[str]:
        # Only accept replies newer than the prompt, from the right chat.
        offset = self._latest_update_id() + 1
        self._post("sendMessage", {"chat_id": self.chat_id, "text": prompt})
        deadline = time.monotonic() + timeout_s
        pattern = re.compile(reply_pattern)
        while time.monotonic() < deadline:
            data = self._get("getUpdates", {"offset": offset, "timeout": 0})
            for upd in data.get("result", []):
                offset = max(offset, upd.get("update_id", 0) + 1)
                msg = upd.get("message") or {}
                if str((msg.get("chat") or {}).get("id")) != self.chat_id:
                    continue
                text = (msg.get("text") or "").strip()
                if pattern.match(text):
                    return text
            time.sleep(self.poll_s)
        return None


def build_interactor(state_dir: Path, env: Optional[dict[str, str]] = None) -> Interactor:
    import os

    e = env if env is not None else dict(os.environ)
    token = e.get("SNIPER_TELEGRAM_BOT_TOKEN")
    chat = e.get("SNIPER_TELEGRAM_CHAT_ID")
    if token and chat:
        return TelegramInteractor(token, chat)
    return FileInteractor(state_dir)
