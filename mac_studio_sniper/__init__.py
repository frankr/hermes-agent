"""Mac Studio refurb sniper — Phase 1: detection & alerting.

Watches Apple's US refurbished store for Mac Studio M3 Ultra listings that
match ``targets.yaml`` and alerts immediately (Telegram / Twilio / console).
The purchase strike path (Phase 2+) hooks in via the watcher's ``on_match``
callback.

Design: ``.plans/mac-studio-refurb-sniper.md``
Success gates: ``.plans/mac-studio-refurb-sniper-goal.md``
G0 recon instructions for the human operator: ``mac_studio_sniper/RECON.md``

Deliberately NOT included in the hermes wheel (see pyproject packages.find)
— this is a personal always-on app, not part of the distributed agent.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
