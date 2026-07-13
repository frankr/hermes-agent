"""Browser-driven buyer/supervisor/heal tests against the in-process fake shop.

Skipped automatically when Playwright or a Chromium binary is unavailable
(e.g. minimal CI). Locally and in the dev sandbox they exercise the real
strike path end to end.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.async_api")

from mac_studio_sniper.buyer import Buyer  # noqa: E402
from mac_studio_sniper.flightplan import Flightplan  # noqa: E402
from mac_studio_sniper.interact import FileInteractor  # noqa: E402
from mac_studio_sniper.matcher import SniperConfig  # noqa: E402
from mac_studio_sniper.models import MatchResult, Tile  # noqa: E402
from mac_studio_sniper.state import StateDB  # noqa: E402

from .fake_shop import FakeShop  # noqa: E402


def _chromium_path() -> str | None:
    for cand in ("/opt/pw-browsers/chromium",):
        if Path(cand).exists():
            return cand
    return None


BROWSER = _chromium_path()
pytestmark = pytest.mark.skipif(
    BROWSER is None and not os.environ.get("SNIPER_USE_SYSTEM_CHROMIUM"),
    reason="no chromium binary available",
)


def _flightplan_yaml(shop: FakeShop, with_cvv: bool = True) -> str:
    cvv_step = (
        """
  - id: payment_cvv
    action: fill
    selectors: ["[data-autom='card-security-code']"]
    value: "{cvv}"
  - id: payment_continue
    action: click
    selectors: ["[data-autom='payment-continue-button']"]
    expect_selector: "[data-autom='place-order-button']"
"""
        if with_cvv
        else ""
    )
    return f"""
version: 1
verified: true
drill_grid_url: "{shop.url('/product')}"
signin_detect_selectors: ["[data-autom='sign-in']"]
session_check:
  url: "{shop.url('/bag-signedin')}"
  signed_in_selectors: ["[data-autom='sign-out']"]
  signed_out_selectors: ["[data-autom='sign-in']"]
steps:
  - id: product
    action: goto
    url: "{{product_url}}"
    expect_selector: "[data-autom='add-to-cart']"
  - id: add_to_bag
    action: click
    selectors: ["[data-autom='add-to-cart']"]
    expect_selector: "[data-autom='checkout']"
  - id: checkout
    action: click
    selectors: ["[data-autom='checkout']"]
    expect_selector: "[data-autom='fulfillment-continue-button']"
  - id: shipping_continue
    action: click
    selectors: ["[data-autom='fulfillment-continue-button']"]
{cvv_step}
  - id: place_order
    action: click
    final: true
    selectors: ["[data-autom='place-order-button']"]
    expect_url: "thankyou"
"""


def _make(tmp_path: Path, shop: FakeShop, mode: str, with_cvv: bool = True):
    (tmp_path / "targets.yaml").write_text(
        """
mode: %s
quantity: 1
stop_after_first_success: true
confirm_timeout_s: 5
targets:
  - name: "M3 Ultra 512GB"
    priority: 1
    match: { chip: "M3 Ultra", ram_gb: 512 }
    max_price_usd: 8600
""" % mode,
        encoding="utf-8",
    )
    (tmp_path / "flightplan.yaml").write_text(_flightplan_yaml(shop, with_cvv), encoding="utf-8")
    config = SniperConfig.load(tmp_path / "targets.yaml")
    flightplan = Flightplan.load(tmp_path / "flightplan.yaml")
    state = StateDB(tmp_path / "state.sqlite")

    class NullNotifier:
        def send_raw(self, text):
            return ["console"]

        def send_match_alert(self, m):
            return ["console"]

        def channels(self):
            return ["console"]

    buyer = Buyer(
        config=config,
        flightplan=flightplan,
        state=state,
        notifier=NullNotifier(),
        interactor=FileInteractor(tmp_path, poll_s=0.05),
        state_dir=tmp_path,
        profile_dir=tmp_path / "profile",
        browser_path=BROWSER,
        headless=True,
    )
    return buyer, config, state


def _match(shop: FakeShop) -> MatchResult:
    tile = Tile(
        part_number="G1/A",
        title="Refurbished Mac Studio Apple M3 Ultra Chip",
        chip="M3 Ultra",
        ram_gb=512,
        price_usd=7999.0,
        url=shop.url("/product"),
    )
    return MatchResult(tile=tile, target_name="M3 Ultra 512GB", priority=1, max_price_usd=8600.0)


@pytest.mark.asyncio
async def test_drill_reaches_final_without_buying(tmp_path, monkeypatch):
    monkeypatch.setenv("SNIPER_CVV", "123")
    with FakeShop() as shop:
        buyer, _, state = _make(tmp_path, shop, mode="confirm")
        result = await buyer.drill(shop.url("/product"))
        assert result.ok, result.error
        assert not result.purchased
        # Drill must stop BEFORE place_order.
        assert "place_order" not in result.steps_completed
        assert state.consecutive_passing_drills() == 1


def _answer_when_asked(tmp_path: Path, name: str, answer: str, timeout_s: float = 20.0):
    """Background thread: wait for the interactor's prompt file, then reply."""
    import threading
    import time

    prompt = tmp_path / "ask" / f"{name}.prompt"
    ans = tmp_path / "ask" / f"{name}.answer"

    def wait_and_write():
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if prompt.exists():
                ans.write_text(answer, encoding="utf-8")
                return
            time.sleep(0.05)

    t = threading.Thread(target=wait_and_write, daemon=True)
    t.start()
    return t


@pytest.mark.asyncio
async def test_confirm_mode_buys_after_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("SNIPER_CVV", "123")
    with FakeShop() as shop:
        buyer, _, state = _make(tmp_path, shop, mode="confirm")
        state.record_drill(mode="drill", ok=True, duration_ms=1000)
        _answer_when_asked(tmp_path, "confirm-buy", "BUY")
        result = await buyer.attempt_purchase(_match(shop))
        assert result.purchased, result.error or result.aborted_reason
        assert state.purchase_count() == 1


@pytest.mark.asyncio
async def test_confirm_timeout_does_not_buy(tmp_path, monkeypatch):
    monkeypatch.setenv("SNIPER_CVV", "123")
    with FakeShop() as shop:
        buyer, _, state = _make(tmp_path, shop, mode="confirm")
        state.record_drill(mode="drill", ok=True, duration_ms=1000)
        # No answer written → confirm times out (confirm_timeout_s=5).
        result = await buyer.attempt_purchase(_match(shop))
        assert not result.purchased
        assert state.purchase_count() == 0


@pytest.mark.asyncio
async def test_guardrail_blocks_unverified_flightplan(tmp_path, monkeypatch):
    monkeypatch.setenv("SNIPER_CVV", "123")
    with FakeShop() as shop:
        buyer, _, state = _make(tmp_path, shop, mode="confirm")
        state.record_drill(mode="drill", ok=True, duration_ms=1000)
        buyer.flightplan.verified = False
        result = await buyer.attempt_purchase(_match(shop))
        assert not result.purchased
        assert result.aborted_reason and "not verified" in result.aborted_reason


@pytest.mark.asyncio
async def test_full_auto_buys_without_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("SNIPER_CVV", "123")
    with FakeShop() as shop:
        buyer, _, state = _make(tmp_path, shop, mode="full-auto")
        state.record_drill(mode="drill", ok=True, duration_ms=1000)
        result = await buyer.attempt_purchase(_match(shop))
        assert result.purchased, result.error or result.aborted_reason


@pytest.mark.asyncio
async def test_broken_selector_produces_artifacts(tmp_path):
    with FakeShop() as shop:
        buyer, _, state = _make(tmp_path, shop, mode="confirm")
        # Break the checkout selector.
        for s in buyer.flightplan.steps:
            if s.id == "checkout":
                s.selectors = ["[data-autom='does-not-exist']"]
                s.timeout_ms = 2000
        result = await buyer.drill(shop.url("/product"))
        assert not result.ok
        assert result.failed_step == "checkout"
        assert result.artifacts_dir and (result.artifacts_dir / "dom.html").exists()
