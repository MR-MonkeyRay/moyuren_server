"""Permission tests for static publishing atomic writes."""

import os
import stat
from pathlib import Path

import pytest

from app.core import fileutil
from scripts.publish_static import copy_atomic, write_atomic


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")


def test_static_text_output_uses_configured_output_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Static text artifacts should not retain NamedTemporaryFile mode 0600."""
    monkeypatch.setattr(fileutil, "_OUTPUT_MODE", 0o644)
    target = tmp_path / "nested" / "data.json"

    write_atomic('{"date":"2026-10-30"}', target)

    assert target.read_text(encoding="utf-8") == '{"date":"2026-10-30"}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_static_image_copy_uses_configured_output_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copied static image artifacts should follow the output permission policy."""
    monkeypatch.setattr(fileutil, "_OUTPUT_MODE", 0o644)
    source = tmp_path / "source.jpg"
    source.write_bytes(b"jpeg payload")
    target = tmp_path / "nested" / "latest.jpg"

    copy_atomic(source, target)

    assert target.read_bytes() == b"jpeg payload"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
