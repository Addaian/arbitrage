"""File-sentinel kill switch (PRD §6.2).

A file at a configured path — default `/var/run/quant/HALT`. If it
exists, the system flattens all positions and refuses to submit any
new orders. Resetting requires deliberate removal of the file by an
operator.

Design:

* **Atomic engage**: write a temp file, `os.rename()` to target. Avoids
  a race where a reader sees a partially-written file.
* **Reason is optional**: the file's contents are ignored by
  `is_engaged()`; readers that want to surface the reason call
  `read_reason()`.
* **Idempotent**: `engage()` when already engaged overwrites silently
  (refreshes the reason). `disengage()` when not engaged is a no-op.

The file sentinel pattern is chosen over a process signal or DB flag
because it's:
1. Trivially inspectable via `ls /var/run/quant/HALT`.
2. Trivially toggleable via `touch` / `rm`.
3. Survives process restarts — matches PRD §6.2 which says "halts
   until file removed".
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


class Killswitch:
    """File-backed kill switch. Thread- and process-safe under POSIX
    rename semantics."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def is_engaged(self) -> bool:
        return self._path.exists()

    def engage(self, reason: str = "") -> None:
        """Mark the switch engaged. The optional `reason` string lands
        in the file for operators to inspect.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = f"{datetime.now(UTC).isoformat()}  {reason}\n" if reason else ""
        # Write to a sibling temp file, then rename — atomic on POSIX.
        fd, tmp_str = tempfile.mkstemp(prefix=".halt-", dir=str(self._path.parent))
        tmp = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            tmp.replace(self._path)
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise

    def disengage(self) -> None:
        """Clear the switch. No-op if not engaged."""
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()

    def read_reason(self) -> str | None:
        """Return the file contents (reason line + timestamp), or None
        if not engaged.
        """
        if not self._path.exists():
            return None
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError:
            return None
