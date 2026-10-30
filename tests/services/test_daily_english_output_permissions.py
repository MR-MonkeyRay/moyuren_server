"""Permission tests for the downloaded daily-English SQLite database."""

import asyncio
import logging
import os
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core import fileutil
from app.services import daily_english
from app.services.daily_english import SQLiteBackend


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")


async def _run_inline(function: Callable[..., Any], *args: Any) -> Any:
    """Run a to_thread target inline for this filesystem-only unit test."""
    return function(*args)


async def test_downloaded_sqlite_uses_configured_output_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final dictionary database should be readable under a 0022-style mode."""
    archive_path = tmp_path / "ecdict.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("stardict.db", b"sqlite payload")

    db_path = tmp_path / "cache" / "stardict.db"
    backend = SQLiteBackend(
        db_path=str(db_path),
        download_url="https://example.invalid/ecdict.zip",
        checksum_sha256="",
        ghproxy_urls=[],
        logger=logging.getLogger("test_daily_english_output_permissions"),
    )
    monkeypatch.setattr(fileutil, "_OUTPUT_MODE", 0o644)
    monkeypatch.setattr(daily_english, "_get_ensure_ready_lock", asyncio.Lock)
    monkeypatch.setattr(daily_english.asyncio, "to_thread", _run_inline)
    monkeypatch.setattr(
        backend,
        "_download_to_tempfile",
        AsyncMock(return_value=archive_path),
    )

    await backend.ensure_ready()

    assert db_path.read_bytes() == b"sqlite payload"
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o644
