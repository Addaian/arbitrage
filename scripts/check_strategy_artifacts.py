"""CI gate: if a PR touches `src/quant/signals/` it must also touch
`docs/strategies/` (Wave 14).

Reads the list of changed files (either from `git diff --name-only
<base>...HEAD` or from stdin) and fails with a non-zero exit if any
signal module changed without a companion strategy doc being added or
updated in the same diff.

Exempted files:
    - `src/quant/signals/__init__.py`  (re-exports, no new strategy)
    - `src/quant/signals/base.py`      (shared protocol / base class)

Usage:
    uv run python scripts/check_strategy_artifacts.py --base origin/main
    # or from an arbitrary diff:
    git diff --name-only main | uv run python scripts/check_strategy_artifacts.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

EXEMPT_SIGNALS = {
    Path("src/quant/signals/__init__.py"),
    Path("src/quant/signals/base.py"),
}

app = typer.Typer(add_completion=False, help="Enforce strategy-doc coupling in CI.")


def _changed_files(base: str | None) -> list[Path]:
    if base is None:
        raw = sys.stdin.read()
        return [Path(line.strip()) for line in raw.splitlines() if line.strip()]
    cmd = ["git", "diff", "--name-only", f"{base}...HEAD"]
    # Input is a CLI-provided ref name passed straight to git — no shell
    # interpolation. Accepting the S603 note.
    out = subprocess.check_output(cmd, text=True)  # noqa: S603
    return [Path(line.strip()) for line in out.splitlines() if line.strip()]


@app.command()
def check(
    base: Annotated[
        str | None,
        typer.Option(
            "--base",
            help="Git ref to compare against (e.g. origin/main). "
            "If omitted, reads file list from stdin.",
        ),
    ] = None,
) -> None:
    changed = _changed_files(base)
    if not changed:
        typer.echo("no changes to check")
        return

    signal_changes = [
        p
        for p in changed
        if p.parts[:3] == ("src", "quant", "signals")
        and p.suffix == ".py"
        and p not in EXEMPT_SIGNALS
    ]
    doc_changes = [
        p for p in changed if p.parts[:2] == ("docs", "strategies") and p.suffix == ".md"
    ]

    if signal_changes and not doc_changes:
        typer.echo(
            "ERROR: this diff touches signal modules but adds/updates no "
            "`docs/strategies/*.md` validation artifact.\n"
            "Changed signal files:",
            err=True,
        )
        for p in signal_changes:
            typer.echo(f"  - {p}", err=True)
        typer.echo(
            "\nRun `uv run python scripts/validate_new_strategy.py` for your "
            "strategy and commit the generated `docs/strategies/<name>.md`.",
            err=True,
        )
        raise typer.Exit(code=1)

    if signal_changes:
        typer.echo(
            f"ok: {len(signal_changes)} signal file(s) changed, "
            f"{len(doc_changes)} strategy doc(s) updated"
        )
    else:
        typer.echo("ok: no signal-module changes")


def main() -> None:
    app()


if __name__ == "__main__":
    try:
        main()
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
