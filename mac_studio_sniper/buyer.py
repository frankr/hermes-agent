"""The Buyer: deterministic Playwright executor for the checkout flightplan.

Modes:
  drill — run every step EXCEPT the final Place-Order step (gates 2.1/2.2);
          records per-step latency and a pass/fail row in the state DB.
  live  — the real strike. Guardrails must return [] (checked here, not
          just in the caller). In confirm mode the human must reply BUY
          before the final step executes; full-auto proceeds directly.

Failure handling: any step deviation screenshots the page, dumps DOM to
<state_dir>/artifacts/<run-ts>/, records the failure, and notifies —
those artifacts are exactly what the supervisor's self-heal loop consumes.

Sign-in bounce: if a signin_detect selector appears where it shouldn't,
the run pauses and brokers the login (password stays human-typed via a
headed session; the 2FA code relays over Telegram — gate 2.4).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .flightplan import Flightplan, Step
from .guardrails import check_arm
from .interact import Interactor
from .matcher import SniperConfig
from .models import MatchResult
from .notify import Notifier
from .secrets import cvv_available, get_cvv
from .state import StateDB

logger = logging.getLogger("mac_studio_sniper.buyer")

DEFAULT_PROFILE_DIR = Path.home() / ".mac_studio_sniper" / "browser-profile"


class StepFailure(Exception):
    def __init__(self, step_id: str, message: str) -> None:
        super().__init__(f"step {step_id!r}: {message}")
        self.step_id = step_id
        self.message = message


@dataclass
class RunResult:
    ok: bool
    mode: str
    duration_ms: float
    steps_completed: list[str] = field(default_factory=list)
    step_timings_ms: dict[str, float] = field(default_factory=dict)
    failed_step: Optional[str] = None
    error: Optional[str] = None
    artifacts_dir: Optional[Path] = None
    purchased: bool = False
    aborted_reason: Optional[str] = None


class Buyer:
    def __init__(
        self,
        config: SniperConfig,
        flightplan: Flightplan,
        state: StateDB,
        notifier: Notifier,
        interactor: Interactor,
        state_dir: Path,
        profile_dir: Path = DEFAULT_PROFILE_DIR,
        browser_path: Optional[str] = None,
        headless: bool = True,
    ) -> None:
        self.config = config
        self.flightplan = flightplan
        self.state = state
        self.notifier = notifier
        self.interactor = interactor
        self.state_dir = state_dir
        self.profile_dir = profile_dir
        self.browser_path = browser_path
        self.headless = headless
        self.kill_switch = state_dir / "KILL"

    # ------------------------------------------------------------------
    # public entry points
    # ------------------------------------------------------------------

    async def drill(self, product_url: str) -> RunResult:
        result = await self._run(product_url, mode="drill")
        self.state.record_drill(
            mode="drill",
            ok=result.ok,
            duration_ms=result.duration_ms,
            failed_step=result.failed_step,
            notes=result.error,
        )
        if not result.ok:
            self.notifier.send_raw(
                f"❌ drill FAILED at step {result.failed_step!r}: {result.error}\n"
                f"artifacts: {result.artifacts_dir}\nSystem is NOT race-ready."
            )
        return result

    async def attempt_purchase(self, match: MatchResult) -> RunResult:
        """The real strike. Guardrails are re-checked HERE, at strike time."""
        violations = check_arm(
            self.config,
            match,
            self.state,
            self.flightplan,
            self.kill_switch,
            cvv_available=cvv_available(),
        )
        if violations:
            reason = "; ".join(violations)
            logger.warning("arming blocked: %s", reason)
            self.notifier.send_raw(
                "🛑 Match found but NOT buying — guardrails:\n- " + "\n- ".join(violations)
            )
            return RunResult(ok=False, mode="live", duration_ms=0, aborted_reason=reason)
        result = await self._run(
            match.tile.product_url, mode="live", match=match
        )
        self.state.record_drill(  # live runs also count into drill telemetry
            mode="live",
            ok=result.ok,
            duration_ms=result.duration_ms,
            failed_step=result.failed_step,
            notes=result.error or result.aborted_reason,
        )
        if result.purchased:
            self.state.record_purchase(
                part_number=match.tile.part_number,
                price_usd=match.tile.price_usd,
                order_ref=None,
                mode=self.config.mode,
            )
            self.notifier.send_raw(
                f"🎉 ORDER PLACED: {match.tile.title} — ${match.tile.price_usd:,.2f}\n"
                "System is now disarmed (stop_after_first_success)."
            )
        return result

    # ------------------------------------------------------------------
    # engine
    # ------------------------------------------------------------------

    async def _run(
        self, product_url: str, mode: str, match: Optional[MatchResult] = None
    ) -> RunResult:
        from playwright.async_api import async_playwright

        started = time.monotonic()
        result = RunResult(ok=False, mode=mode, duration_ms=0)
        run_stamp = time.strftime("%Y%m%d-%H%M%S")
        context_vars = {"product_url": product_url}
        cvv = get_cvv()
        if cvv:
            context_vars["cvv"] = cvv

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=self.headless,
                executable_path=self.browser_path or None,
                viewport={"width": 1440, "height": 900},
            )
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                for step in self.flightplan.steps:
                    if self.kill_switch.exists():
                        result.aborted_reason = "kill switch"
                        result.error = "kill switch present — aborted"
                        return result
                    if step.final:
                        if mode == "drill":
                            # Drill success: we reached the final button without
                            # executing it.
                            result.ok = True
                            return result
                        if not await self._confirm_final(match):
                            result.aborted_reason = "not confirmed"
                            result.error = "confirm window elapsed without BUY"
                            return result
                        # Guardrails once more, at the last possible moment.
                        if match is not None:
                            violations = check_arm(
                                self.config,
                                match,
                                self.state,
                                self.flightplan,
                                self.kill_switch,
                                cvv_available=True,  # already substituted
                            )
                            if violations:
                                result.aborted_reason = "; ".join(violations)
                                result.error = result.aborted_reason
                                return result
                    step_start = time.monotonic()
                    try:
                        await self._execute_step(page, step, context_vars)
                    except StepFailure as e:
                        if await self._maybe_signin_recovery(page):
                            await self._execute_step(page, step, context_vars)
                        else:
                            raise e
                    result.step_timings_ms[step.id] = (time.monotonic() - step_start) * 1000
                    result.steps_completed.append(step.id)
                    if step.final:
                        result.purchased = True
                result.ok = True
                return result
            except StepFailure as e:
                result.failed_step = e.step_id
                result.error = e.message
                result.artifacts_dir = await self._capture_artifacts(page, run_stamp, e)
                final = self.flightplan.final_step
                if mode == "live" and final and e.step_id == final.id:
                    # The Place-Order click may have gone through even though
                    # the post-click assertion failed. Human must check NOW.
                    self.notifier.send_raw(
                        "⚠️⚠️ FINAL STEP FAILED AFTER THE ORDER CLICK — order state"
                        " UNKNOWN. Check your email and apple.com order history"
                        f" immediately. Artifacts: {result.artifacts_dir}"
                    )
                return result
            except Exception as e:  # browser died, timeout at engine level…
                result.failed_step = result.failed_step or "(engine)"
                result.error = f"{type(e).__name__}: {e}"
                try:
                    result.artifacts_dir = await self._capture_artifacts(page, run_stamp, e)
                except Exception:
                    pass
                return result
            finally:
                result.duration_ms = (time.monotonic() - started) * 1000
                await context.close()

    async def _execute_step(self, page: Any, step: Step, context_vars: dict[str, str]) -> None:
        timeout = step.timeout_ms
        if step.action == "goto":
            url = _render(step.url or "", context_vars, step.id)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        elif step.action == "sleep":
            await asyncio.sleep(step.sleep_ms / 1000)
        elif step.action in ("click", "fill", "assert"):
            locator = await self._first_visible(page, step)
            if locator is None:
                if step.optional:
                    logger.info("optional step %s: no selector present, skipping", step.id)
                    return
                raise StepFailure(step.id, f"none of {step.selectors} became visible")
            if step.action == "click":
                await locator.click(timeout=timeout)
            elif step.action == "fill":
                await locator.fill(_render(step.value or "", context_vars, step.id), timeout=timeout)
            # assert: visibility already proven
        else:  # pragma: no cover - schema-validated
            raise StepFailure(step.id, f"unknown action {step.action}")

        if step.expect_url:
            try:
                await page.wait_for_url(f"**{step.expect_url}**", timeout=timeout)
            except Exception:
                raise StepFailure(
                    step.id, f"URL never contained {step.expect_url!r} (at {page.url})"
                ) from None
        if step.expect_selector:
            try:
                await page.locator(step.expect_selector).first.wait_for(
                    state="visible", timeout=timeout
                )
            except Exception:
                raise StepFailure(
                    step.id, f"expected element {step.expect_selector!r} never appeared"
                ) from None

    async def _first_visible(self, page: Any, step: Step) -> Optional[Any]:
        """Try selectors in order; return the first that becomes visible."""
        per_selector = max(step.timeout_ms // max(len(step.selectors), 1), 1500)
        for sel in step.selectors:
            loc = page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=per_selector)
                return loc
            except Exception:
                continue
        return None

    async def _maybe_signin_recovery(self, page: Any) -> bool:
        """Detect a mid-flow bounce to sign-in and broker it (gate 2.4)."""
        for sel in self.flightplan.signin_detect_selectors:
            try:
                if await page.locator(sel).first.is_visible():
                    break
            except Exception:
                continue
        else:
            return False
        self.notifier.send_raw(
            "🔐 Apple bounced the session to sign-in mid-flow. If a 2FA prompt"
            " is showing, reply with the 6-digit code."
        )
        code = await asyncio.to_thread(
            self.interactor.ask,
            "signin-2fa",
            "Reply with the 6-digit Apple ID verification code:",
            r"^\d{6}$",
            300,
        )
        if not code:
            return False
        # Best-effort: Apple's 2FA is 6 individual inputs or one field.
        try:
            single = page.locator("input[autocomplete='one-time-code']").first
            if await single.is_visible():
                await single.fill(code)
                return True
            boxes = page.locator("input.form-security-code-input")
            n = await boxes.count()
            if n == 6:
                for i, digit in enumerate(code):
                    await boxes.nth(i).fill(digit)
                return True
        except Exception:
            logger.exception("2FA code entry failed")
        return False

    async def _confirm_final(self, match: Optional[MatchResult]) -> bool:
        if self.config.mode == "full-auto":
            return True
        headline = match.headline() if match else "(no match context)"
        answer = await asyncio.to_thread(
            self.interactor.ask,
            "confirm-buy",
            f"🟢 Ready to place order:\n{headline}\n"
            f"Reply BUY within {self.config.confirm_timeout_s}s to complete.",
            r"^\s*BUY\s*$",
            float(self.config.confirm_timeout_s),
        )
        return answer is not None

    async def _capture_artifacts(self, page: Any, run_stamp: str, err: Exception) -> Path:
        out = self.state_dir / "artifacts" / run_stamp
        out.mkdir(parents=True, exist_ok=True)
        (out / "error.txt").write_text(str(err), encoding="utf-8")
        try:
            await page.screenshot(path=str(out / "failure.png"), full_page=True)
        except Exception:
            pass
        try:
            (out / "dom.html").write_text(await page.content(), encoding="utf-8")
            (out / "url.txt").write_text(page.url, encoding="utf-8")
        except Exception:
            pass
        return out


def _render(template: str, context_vars: dict[str, str], step_id: str) -> str:
    try:
        return template.format(**context_vars)
    except KeyError as e:
        raise StepFailure(step_id, f"missing template variable {e} (secret not configured?)") from None
