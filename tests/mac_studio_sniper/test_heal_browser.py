"""Gate 4.3: self-heal promotes a candidate flightplan only after a
passing drill, and rejects a candidate that still fails."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.async_api")

from mac_studio_sniper.buyer import Buyer  # noqa: E402
from mac_studio_sniper.flightplan import Flightplan  # noqa: E402
from mac_studio_sniper.heal import attempt_heal  # noqa: E402
from mac_studio_sniper.interact import FileInteractor  # noqa: E402
from mac_studio_sniper.matcher import SniperConfig  # noqa: E402
from mac_studio_sniper.state import StateDB  # noqa: E402

from .fake_shop import FakeShop  # noqa: E402


def _chromium_path():
    return "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None


BROWSER = _chromium_path()
pytestmark = pytest.mark.skipif(
    BROWSER is None and not os.environ.get("SNIPER_USE_SYSTEM_CHROMIUM"),
    reason="no chromium binary available",
)


class NullNotifier:
    def send_raw(self, text):
        return ["console"]

    def channels(self):
        return ["console"]


def _broken_and_fixed_plans(shop: FakeShop):
    # 'checkout' step has a WRONG selector — drill fails there.
    broken = f"""
version: 1
verified: true
drill_grid_url: "{shop.url('/product')}"
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
    selectors: ["[data-autom='WRONG-selector']"]
    timeout_ms: 2000
  - id: place_order
    action: click
    final: true
    selectors: ["[data-autom='place-order-button']"]
"""
    fixed = broken.replace("[data-autom='WRONG-selector']", "[data-autom='checkout']")
    return broken, fixed


def _config(tmp_path: Path) -> SniperConfig:
    (tmp_path / "targets.yaml").write_text(
        """
mode: confirm
targets:
  - name: "M3 Ultra 512GB"
    priority: 1
    match: { chip: "M3 Ultra", ram_gb: 512 }
    max_price_usd: 8600
""",
        encoding="utf-8",
    )
    return SniperConfig.load(tmp_path / "targets.yaml")


def _buyer_factory(tmp_path, shop, config, state):
    def make(flightplan: Flightplan) -> Buyer:
        return Buyer(
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

    return make


@pytest.mark.asyncio
async def test_heal_promotes_fixed_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("SNIPER_CVV", "123")
    with FakeShop() as shop:
        broken, fixed = _broken_and_fixed_plans(shop)
        live = tmp_path / "flightplan.yaml"
        live.write_text(broken, encoding="utf-8")
        config = _config(tmp_path)
        state = StateDB(tmp_path / "state.sqlite")
        fp = Flightplan.load(live)

        async def heal_fn(bundle_dir):
            # A real agent reads bundle_dir/dom.html; here we return the fix.
            assert (bundle_dir / "brief.json").exists()
            return fixed

        outcome = await attempt_heal(
            state_dir=tmp_path,
            flightplan=fp,
            buyer_factory=_buyer_factory(tmp_path, shop, config, state),
            heal_fn=heal_fn,
            failed_step="checkout",
            error="selector not found",
            artifacts_dir=None,
            drill_product_url=shop.url("/product"),
            git_commit=False,
        )
        assert outcome.healed
        assert outcome.drill_ok
        # Live file now carries the fixed selector.
        assert "[data-autom='checkout']" in live.read_text(encoding="utf-8")
        assert "WRONG-selector" not in live.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_heal_rejects_still_broken_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("SNIPER_CVV", "123")
    with FakeShop() as shop:
        broken, _ = _broken_and_fixed_plans(shop)
        live = tmp_path / "flightplan.yaml"
        live.write_text(broken, encoding="utf-8")
        config = _config(tmp_path)
        state = StateDB(tmp_path / "state.sqlite")
        fp = Flightplan.load(live)

        async def heal_fn(bundle_dir):
            # Agent proposes a still-wrong selector.
            return broken.replace("WRONG-selector", "STILL-WRONG")

        outcome = await attempt_heal(
            state_dir=tmp_path,
            flightplan=fp,
            buyer_factory=_buyer_factory(tmp_path, shop, config, state),
            heal_fn=heal_fn,
            failed_step="checkout",
            error="selector not found",
            artifacts_dir=None,
            drill_product_url=shop.url("/product"),
            git_commit=False,
        )
        assert not outcome.healed
        # Live file must be UNTOUCHED (still the original broken selector).
        assert "WRONG-selector" in live.read_text(encoding="utf-8")
        assert "STILL-WRONG" not in live.read_text(encoding="utf-8")
