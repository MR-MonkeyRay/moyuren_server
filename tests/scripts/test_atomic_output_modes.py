"""静态发布原子写入的权限测试。"""

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
    """静态文本产物不应保留 NamedTemporaryFile 的 0600 权限。"""
    monkeypatch.setattr(fileutil, "_OUTPUT_MODE", 0o644)
    target = tmp_path / "nested" / "data.json"

    write_atomic('{"date":"2026-10-30"}', target)

    assert target.read_text(encoding="utf-8") == '{"date":"2026-10-30"}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_static_image_copy_uses_configured_output_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复制的静态图片产物应遵循输出权限策略。"""
    monkeypatch.setattr(fileutil, "_OUTPUT_MODE", 0o644)
    source = tmp_path / "source.jpg"
    source.write_bytes(b"jpeg payload")
    target = tmp_path / "nested" / "latest.jpg"

    copy_atomic(source, target)

    assert target.read_bytes() == b"jpeg payload"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
