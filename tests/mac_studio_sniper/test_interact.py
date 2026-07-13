import threading
import time
from pathlib import Path

from mac_studio_sniper.interact import FileInteractor, TelegramInteractor


def test_file_interactor_matching_answer(tmp_path: Path):
    it = FileInteractor(tmp_path, poll_s=0.02)
    result = {}

    def ask():
        result["v"] = it.ask("confirm-buy", "reply BUY", r"^\s*BUY\s*$", timeout_s=3)

    t = threading.Thread(target=ask)
    t.start()
    time.sleep(0.1)
    (tmp_path / "ask" / "confirm-buy.answer").write_text("BUY", encoding="utf-8")
    t.join()
    assert result["v"] == "BUY"


def test_file_interactor_timeout(tmp_path: Path):
    it = FileInteractor(tmp_path, poll_s=0.02)
    assert it.ask("x", "prompt", r"^BUY$", timeout_s=0.2) is None


def test_file_interactor_non_matching_ignored(tmp_path: Path):
    it = FileInteractor(tmp_path, poll_s=0.02)
    result = {}

    def ask():
        result["v"] = it.ask("code", "code?", r"^\d{6}$", timeout_s=2)

    t = threading.Thread(target=ask)
    t.start()
    time.sleep(0.1)
    ans = tmp_path / "ask" / "code.answer"
    ans.write_text("nope", encoding="utf-8")
    time.sleep(0.2)
    ans.write_text("123456", encoding="utf-8")
    t.join()
    assert result["v"] == "123456"


def test_telegram_interactor_with_fake_transport():
    sent = []
    updates = {"n": 0}

    def http_post(method, payload):
        sent.append((method, payload))
        return {"ok": True}

    def http_get(method, params):
        # First call establishes offset baseline; later a matching reply lands.
        updates["n"] += 1
        if updates["n"] <= 1:
            return {"result": []}
        return {
            "result": [
                {
                    "update_id": 100,
                    "message": {"chat": {"id": 42}, "text": "BUY"},
                }
            ]
        }

    it = TelegramInteractor("tok", "42", http_get=http_get, http_post=http_post, poll_s=0.01)
    answer = it.ask("confirm", "reply BUY", r"^\s*BUY\s*$", timeout_s=2)
    assert answer == "BUY"
    assert sent and sent[0][0] == "sendMessage"


def test_telegram_ignores_other_chats():
    def http_post(method, payload):
        return {"ok": True}

    def http_get(method, params):
        return {
            "result": [
                {"update_id": 5, "message": {"chat": {"id": 999}, "text": "BUY"}}
            ]
        }

    it = TelegramInteractor("tok", "42", http_get=http_get, http_post=http_post, poll_s=0.01)
    assert it.ask("confirm", "p", r"^BUY$", timeout_s=0.3) is None
