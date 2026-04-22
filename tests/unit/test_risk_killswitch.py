"""Tests for the file-sentinel Killswitch."""

from __future__ import annotations

from pathlib import Path

import pytest

from quant.risk import Killswitch


def test_not_engaged_by_default(tmp_path: Path) -> None:
    k = Killswitch(tmp_path / "HALT")
    assert not k.is_engaged()


def test_engage_creates_file(tmp_path: Path) -> None:
    k = Killswitch(tmp_path / "HALT")
    k.engage()
    assert k.is_engaged()
    assert k.path.exists()


def test_engage_with_reason_records_it(tmp_path: Path) -> None:
    k = Killswitch(tmp_path / "HALT")
    k.engage(reason="daily loss breach")
    reason = k.read_reason()
    assert reason is not None
    assert "daily loss breach" in reason


def test_engage_creates_parent_directory(tmp_path: Path) -> None:
    k = Killswitch(tmp_path / "nested" / "dir" / "HALT")
    k.engage()
    assert k.is_engaged()


def test_engage_is_idempotent(tmp_path: Path) -> None:
    k = Killswitch(tmp_path / "HALT")
    k.engage(reason="first")
    k.engage(reason="second")  # overwrites
    reason = k.read_reason()
    assert reason is not None
    assert "second" in reason
    assert "first" not in reason


def test_disengage_removes_file(tmp_path: Path) -> None:
    k = Killswitch(tmp_path / "HALT")
    k.engage()
    k.disengage()
    assert not k.is_engaged()


def test_disengage_is_idempotent(tmp_path: Path) -> None:
    k = Killswitch(tmp_path / "HALT")
    k.disengage()  # no-op
    k.disengage()  # still no-op
    assert not k.is_engaged()


def test_read_reason_none_when_not_engaged(tmp_path: Path) -> None:
    k = Killswitch(tmp_path / "HALT")
    assert k.read_reason() is None


def test_read_reason_empty_body_when_no_reason(tmp_path: Path) -> None:
    k = Killswitch(tmp_path / "HALT")
    k.engage()  # no reason
    reason = k.read_reason()
    assert reason == ""


def test_path_property(tmp_path: Path) -> None:
    path = tmp_path / "HALT"
    k = Killswitch(path)
    assert k.path == path


def test_read_reason_returns_none_on_os_error(tmp_path: Path, monkeypatch) -> None:
    k = Killswitch(tmp_path / "HALT")
    k.engage(reason="test")

    # Simulate a mid-read OSError by patching read_text.
    def _raise(*_args, **_kwargs):
        raise OSError("simulated")

    monkeypatch.setattr(Path, "read_text", _raise)
    assert k.read_reason() is None


def test_engage_cleanup_on_rename_failure(tmp_path: Path, monkeypatch) -> None:
    """If the atomic rename fails, the temp file is removed — no stale
    debris in the parent directory.
    """
    k = Killswitch(tmp_path / "HALT")

    original_replace = Path.replace

    def _fail_replace(self, target, *args, **kwargs):
        raise OSError("cross-device rename")

    monkeypatch.setattr(Path, "replace", _fail_replace)

    with pytest.raises(OSError, match="cross-device"):
        k.engage(reason="boom")

    # Restore (important) and verify no .halt-* leftovers.
    monkeypatch.setattr(Path, "replace", original_replace)
    leftovers = list(tmp_path.glob(".halt-*"))
    assert leftovers == []
