"""Flightplan: the externalized checkout script.

Selectors, URLs, and assertions live in ``flightplan.yaml`` — not code —
so the supervisor's self-heal loop can patch them without a deploy, and
so G0 recon output can be filled in without touching the executor.

The ``verified`` flag is the G0 seam: it ships ``false`` with placeholder
selectors, and guardrails hard-block any live purchase while it is false.
Flipping it to ``true`` is done by a human (or the supervisor after a
passing drill) once recon-derived selectors are in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

VALID_ACTIONS = {"goto", "click", "fill", "assert", "sleep"}


@dataclass
class Step:
    id: str
    action: str
    selectors: list[str] = field(default_factory=list)
    url: Optional[str] = None            # for goto; supports {product_url}
    value: Optional[str] = None          # for fill; supports {cvv} etc.
    expect_url: Optional[str] = None     # substring the post-step URL must contain
    expect_selector: Optional[str] = None  # element that must appear after the step
    timeout_ms: int = 10_000
    optional: bool = False               # missing element skips instead of fails
    final: bool = False                  # the Place-Order step: drill stops before it
    sleep_ms: int = 0

    def validate(self) -> list[str]:
        problems = []
        if self.action not in VALID_ACTIONS:
            problems.append(f"step {self.id}: unknown action {self.action!r}")
        if self.action in ("click", "fill", "assert") and not self.selectors:
            problems.append(f"step {self.id}: action {self.action!r} requires selectors")
        if self.action == "goto" and not self.url:
            problems.append(f"step {self.id}: goto requires url")
        if self.action == "fill" and self.value is None:
            problems.append(f"step {self.id}: fill requires value")
        return problems


@dataclass
class SessionCheck:
    url: str
    signed_in_selectors: list[str]
    signed_out_selectors: list[str]
    timeout_ms: int = 15_000


@dataclass
class Flightplan:
    version: int
    verified: bool
    steps: list[Step]
    session_check: Optional[SessionCheck] = None
    drill_grid_url: Optional[str] = None   # where the drill finds a cheap target
    signin_detect_selectors: list[str] = field(default_factory=list)
    notes: str = ""
    path: Optional[Path] = None

    @property
    def final_step(self) -> Optional[Step]:
        finals = [s for s in self.steps if s.final]
        return finals[0] if finals else None

    def uses_placeholder(self, name: str) -> bool:
        needle = "{" + name + "}"
        return any((s.value and needle in s.value) or (s.url and needle in s.url) for s in self.steps)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.steps:
            problems.append("flightplan has no steps")
        seen_ids: set[str] = set()
        for s in self.steps:
            if s.id in seen_ids:
                problems.append(f"duplicate step id {s.id!r}")
            seen_ids.add(s.id)
            problems.extend(s.validate())
        finals = [s for s in self.steps if s.final]
        if len(finals) > 1:
            problems.append("multiple steps marked final — exactly one Place-Order step allowed")
        if finals and self.steps[-1] is not finals[0]:
            problems.append("the final step must be the last step")
        return problems

    @classmethod
    def load(cls, path: Path) -> "Flightplan":
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        steps = [
            Step(
                id=str(s["id"]),
                action=str(s["action"]),
                selectors=list(s.get("selectors", [])),
                url=s.get("url"),
                value=s.get("value"),
                expect_url=s.get("expect_url"),
                expect_selector=s.get("expect_selector"),
                timeout_ms=int(s.get("timeout_ms", 10_000)),
                optional=bool(s.get("optional", False)),
                final=bool(s.get("final", False)),
                sleep_ms=int(s.get("sleep_ms", 0)),
            )
            for s in data.get("steps", [])
        ]
        sc = None
        if data.get("session_check"):
            raw = data["session_check"]
            sc = SessionCheck(
                url=raw["url"],
                signed_in_selectors=list(raw.get("signed_in_selectors", [])),
                signed_out_selectors=list(raw.get("signed_out_selectors", [])),
                timeout_ms=int(raw.get("timeout_ms", 15_000)),
            )
        fp = cls(
            version=int(data.get("version", 1)),
            verified=bool(data.get("verified", False)),
            steps=steps,
            session_check=sc,
            drill_grid_url=data.get("drill_grid_url"),
            signin_detect_selectors=list(data.get("signin_detect_selectors", [])),
            notes=str(data.get("notes", "")),
            path=path,
        )
        problems = fp.validate()
        if problems:
            raise ValueError(f"{path}: invalid flightplan: " + "; ".join(problems))
        return fp
