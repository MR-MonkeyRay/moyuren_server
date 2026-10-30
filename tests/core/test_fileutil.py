"""原子输出的权限、内容完整性与清理保证。"""

import os
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core import fileutil
from app.core.errors import StorageError


@pytest.mark.parametrize("content", ["中文\n", b"\x00\xff\n"])
@pytest.mark.parametrize("sync", [False, True])
def test_text_and_bytes(tmp_path: Path, content: str | bytes, sync: bool) -> None:
    target = tmp_path / "output"
    target.write_bytes(b"old")
    fileutil.write_atomic(target, content, fsync=sync)
    expected = content.encode("utf-8") if isinstance(content, str) else content
    assert target.read_bytes() == expected
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
@pytest.mark.parametrize("mask", [0o022, 0o027, 0o077])
@pytest.mark.parametrize("content", ["文本", b"bytes"])
def test_permissions_follow_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mask: int, content: str | bytes,
) -> None:
    previous = os.umask(mask)
    try:
        # 注入全新的启动时快照，避免重新加载已导入的模块。
        monkeypatch.setattr(fileutil, "_OUTPUT_MODE", 0o666 & ~fileutil._read_umask())
        target = tmp_path / "output"
        target.write_bytes(b"previous")
        target.chmod(0o600)
        real_replace = os.replace

        def checked_replace(source: Path, destination: Path) -> None:
            assert target.read_bytes() == b"previous"
            assert stat.S_IMODE(source.stat().st_mode) == 0o666 & ~mask
            real_replace(source, destination)

        monkeypatch.setattr(fileutil.os, "replace", checked_replace)
        fileutil.write_atomic(target, content)
        assert stat.S_IMODE(target.stat().st_mode) == 0o666 & ~mask
        assert list(tmp_path.iterdir()) == [target]
    finally:
        os.umask(previous)


@pytest.mark.parametrize("stage", ["write", "chmod", "fsync", "replace"])
def test_failure_preserves_target_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    target = tmp_path / "output"
    target.write_bytes(b"previous")
    content = "\ud800" if stage == "write" else "replacement"
    if stage != "write":
        monkeypatch.setattr(fileutil.os, stage, Mock(side_effect=OSError("disk failure")))
    with pytest.raises((OSError, UnicodeError)):
        fileutil.write_atomic(target, content, fsync=True)
    assert target.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [target]


def test_proc_umask_does_not_mutate_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "read_text", Mock(return_value="Name:\tpython\nUmask:\t0027\n"))
    mutate = Mock(side_effect=AssertionError("must not mutate process umask"))
    monkeypatch.setattr(fileutil.os, "umask", mutate)
    assert fileutil._read_umask() == 0o027
    mutate.assert_not_called()


def test_portable_umask_fallback_restores_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=FileNotFoundError))
    mutate = Mock(side_effect=[0o027, 0])
    monkeypatch.setattr(fileutil.os, "umask", mutate)
    assert fileutil._read_umask() == 0o027
    assert [call.args for call in mutate.call_args_list] == [(0,), (0o027,)]


def test_portable_umask_fallback_refuses_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=FileNotFoundError))
    monkeypatch.setattr(fileutil.threading, "current_thread", Mock(return_value=object()))
    mutate = Mock()
    monkeypatch.setattr(fileutil.os, "umask", mutate)
    with pytest.raises(StorageError, match="main thread"):
        fileutil._read_umask()
    mutate.assert_not_called()
