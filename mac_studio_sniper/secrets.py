"""Secret access (currently just the card CVV for checkout).

Sources, in order:
  1. env SNIPER_CVV (e.g. injected by systemd LoadCredential / keyring shim)
  2. ~/.mac_studio_sniper/cvv — must be chmod 600, refused otherwise

Never logged, never stored in the state DB, only ever substituted into a
fill step at execution time.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional

CVV_FILE = Path.home() / ".mac_studio_sniper" / "cvv"


def get_cvv(env: Optional[dict[str, str]] = None, cvv_file: Path = CVV_FILE) -> Optional[str]:
    e = env if env is not None else dict(os.environ)
    if e.get("SNIPER_CVV"):
        return e["SNIPER_CVV"].strip()
    if cvv_file.exists():
        mode = stat.S_IMODE(cvv_file.stat().st_mode)
        if mode & 0o077:
            raise PermissionError(
                f"{cvv_file} is readable by group/other (mode {oct(mode)}) — chmod 600 it"
            )
        return cvv_file.read_text(encoding="utf-8").strip() or None
    return None


def cvv_available(env: Optional[dict[str, str]] = None, cvv_file: Path = CVV_FILE) -> bool:
    try:
        return get_cvv(env=env, cvv_file=cvv_file) is not None
    except PermissionError:
        return False
