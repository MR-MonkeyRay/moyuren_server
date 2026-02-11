"""Tests for app.core.filelock module."""

import asyncio
from pathlib import Path

import pytest

from app.core.filelock import FileLockTimeout, async_file_lock


class TestAsyncFileLock:
    """Tests for async_file_lock context manager."""

    @pytest.mark.asyncio
    async def test_acquire_and_release(self, tmp_path: Path) -> None:
        """锁获取后释放，应能再次获取。"""
        lock_file = tmp_path / "test.lock"

        async with async_file_lock(lock_file, timeout=1.0):
            assert lock_file.exists()

        # 释放后应能再次获取
        async with async_file_lock(lock_file, timeout=1.0):
            pass

    @pytest.mark.asyncio
    async def test_timeout_on_contention(self, tmp_path: Path) -> None:
        """锁被持有时，第二个请求应超时。"""
        lock_file = tmp_path / "test.lock"

        async def holder():
            async with async_file_lock(lock_file, timeout=5.0):
                await asyncio.sleep(2.0)

        async def waiter():
            await asyncio.sleep(0.1)  # 确保 holder 先获取
            with pytest.raises(FileLockTimeout):
                async with async_file_lock(lock_file, timeout=0.3):
                    pass

        await asyncio.gather(holder(), waiter())

    @pytest.mark.asyncio
    async def test_release_on_cancellation(self, tmp_path: Path) -> None:
        """协程被取消时，锁应正确释放。"""
        lock_file = tmp_path / "test.lock"

        async def hold_lock():
            async with async_file_lock(lock_file, timeout=5.0):
                await asyncio.sleep(10.0)  # 会被取消

        task = asyncio.create_task(hold_lock())
        await asyncio.sleep(0.1)  # 等待锁获取
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        # 取消后应能再次获取
        async with async_file_lock(lock_file, timeout=1.0):
            pass

    @pytest.mark.asyncio
    async def test_invalid_timeout(self, tmp_path: Path) -> None:
        """负数 timeout 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="timeout must be non-negative"):
            async with async_file_lock(tmp_path / "test.lock", timeout=-1):
                pass

    @pytest.mark.asyncio
    async def test_invalid_poll_interval(self, tmp_path: Path) -> None:
        """非正数 poll_interval 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="poll_interval must be positive"):
            async with async_file_lock(tmp_path / "test.lock", poll_interval=0):
                pass
