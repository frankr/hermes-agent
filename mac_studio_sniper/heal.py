"""Self-heal seam (gate 4.3).

When a drill fails on a selector/interstitial, the supervisor bundles the
failure artifacts (screenshot path, DOM, the failing step, current
selectors) into a self-contained brief for a Claude agent. The agent's job
is to propose a patched ``flightplan.yaml``; this module then:

  1. loads the proposed plan and structurally validates it,
  2. re-runs a drill against it,
  3. promotes it (writes the file, git-commits) ONLY if the drill passes.

The LLM never touches the live flightplan directly and never touches
targets.yaml. Promotion is gated on a passing drill — a verified change,
not a hopeful one. This file implements steps 1–3 and the bundle format;
the actual "call Claude" step is intentionally pluggable (``heal_fn``) so
it runs under the Claude Agent SDK, a Hermes subagent, or a human.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .buyer import Buyer
from .flightplan import Flightplan

logger = logging.getLogger("mac_studio_sniper.heal")

# heal_fn(bundle_dir) -> patched flightplan YAML text (or None to give up)
HealFn = Callable[[Path], Awaitable[Optional[str]]]


@dataclass
class HealOutcome:
    healed: bool
    bundle_dir: Path
    promoted_path: Optional[Path] = None
    drill_ok: bool = False
    notes: str = ""


def build_heal_bundle(
    state_dir: Path,
    flightplan: Flightplan,
    failed_step: Optional[str],
    error: Optional[str],
    artifacts_dir: Optional[Path],
) -> Path:
    """Assemble a self-contained brief for the healing agent."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bundle = state_dir / "heal" / stamp
    bundle.mkdir(parents=True, exist_ok=True)

    if flightplan.path and flightplan.path.exists():
        shutil.copy(flightplan.path, bundle / "flightplan.current.yaml")
    if artifacts_dir and artifacts_dir.exists():
        for name in ("failure.png", "dom.html", "url.txt", "error.txt"):
            src = artifacts_dir / name
            if src.exists():
                shutil.copy(src, bundle / name)

    failing = next((s for s in flightplan.steps if s.id == failed_step), None)
    brief = {
        "task": (
            "The checkout flightplan failed on the step below. Apple likely "
            "changed the page. Propose a corrected flightplan.yaml. Only change "
            "selectors / expect_url / expect_selector / optional flags for the "
            "failing step (and adjacent steps if clearly needed). Do NOT change "
            "step ids, the final-step marker, or add/remove steps unless the "
            "flow structure demonstrably changed. Prefer Apple 'data-autom' "
            "attributes visible in dom.html. Return the COMPLETE flightplan.yaml."
        ),
        "failed_step": failed_step,
        "error": error,
        "failing_step_spec": None
        if failing is None
        else {
            "id": failing.id,
            "action": failing.action,
            "selectors": failing.selectors,
            "expect_url": failing.expect_url,
            "expect_selector": failing.expect_selector,
        },
        "artifacts": {
            "dom": "dom.html" if (bundle / "dom.html").exists() else None,
            "screenshot": "failure.png" if (bundle / "failure.png").exists() else None,
            "url_at_failure": (bundle / "url.txt").read_text(encoding="utf-8")
            if (bundle / "url.txt").exists()
            else None,
        },
    }
    (bundle / "brief.json").write_text(json.dumps(brief, indent=2), encoding="utf-8")
    logger.info("heal bundle at %s (failing step %s)", bundle, failed_step)
    return bundle


async def attempt_heal(
    state_dir: Path,
    flightplan: Flightplan,
    buyer_factory: Callable[[Flightplan], Buyer],
    heal_fn: HealFn,
    failed_step: Optional[str],
    error: Optional[str],
    artifacts_dir: Optional[Path],
    drill_product_url: str,
    git_commit: bool = True,
) -> HealOutcome:
    bundle = build_heal_bundle(state_dir, flightplan, failed_step, error, artifacts_dir)

    proposed_yaml = await heal_fn(bundle)
    if not proposed_yaml:
        return HealOutcome(healed=False, bundle_dir=bundle, notes="healing agent produced no patch")

    candidate_path = bundle / "flightplan.candidate.yaml"
    candidate_path.write_text(proposed_yaml, encoding="utf-8")
    try:
        candidate = Flightplan.load(candidate_path)
    except ValueError as e:
        return HealOutcome(healed=False, bundle_dir=bundle, notes=f"candidate invalid: {e}")

    # Verify by drilling the CANDIDATE before it touches the live file.
    drill_result = await buyer_factory(candidate).drill(drill_product_url)
    if not drill_result.ok:
        return HealOutcome(
            healed=False,
            bundle_dir=bundle,
            drill_ok=False,
            notes=f"candidate drill failed at {drill_result.failed_step}: {drill_result.error}",
        )

    # Promote: write to the live path, optionally commit for history.
    live_path = flightplan.path
    if live_path is None:
        return HealOutcome(healed=False, bundle_dir=bundle, notes="live flightplan has no path")
    live_path.write_text(proposed_yaml, encoding="utf-8")
    if git_commit:
        _git_commit_flightplan(live_path, failed_step)
    return HealOutcome(
        healed=True,
        bundle_dir=bundle,
        promoted_path=live_path,
        drill_ok=True,
        notes="candidate drill passed; promoted",
    )


def _git_commit_flightplan(path: Path, failed_step: Optional[str]) -> None:
    try:
        subprocess.run(["git", "add", str(path)], cwd=path.parent, check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"heal: patch flightplan after step {failed_step!r} failure (drill-verified)",
            ],
            cwd=path.parent,
            check=True,
            capture_output=True,
        )
    except Exception:
        logger.exception("git commit of healed flightplan failed (non-fatal)")
