"""基于 fcntl.flock 的异步文件锁实现

仅支持 Unix 系统（fcntl），使用非阻塞模式 + 轮询实现异步锁获取。
fd 在事件循环线程同步打开/关闭，避免 thread-local 和取消泄漏问题。
"""

import asyncio
import errno
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

try:
    import fcntl
except ImportError as e:
    raise RuntimeError(
        "async_file_lock requires fcntl (Unix only). "
        "Windows is not supported."
    ) from e

logger = logging.getLogger(__name__)


class FileLockError(Exception):
    """文件锁基础异常"""


class FileLockTimeout(FileLockError):
    """文件锁获取超时"""


@asynccontextmanager
async def async_file_lock(
    lock_path: Path,
    timeout: float = 5.0,
    poll_interval: float = 0.05,
) -> AsyncIterator[None]:
    """异步文件锁 context manager

    使用 fcntl.flock 实现跨进程文件锁，通过非阻塞模式 + 轮询实现异步等待。
    文件描述符在主线程管理，避免 thread-local 问题。

    Args:
        lock_path: 锁文件路径
        timeout: 获取锁的超时时间（秒），默认 5.0
        poll_interval: 轮询间隔（秒），默认 0.05

    Raises:
        FileLockTimeout: 超时未能获取锁
        FileLockError: 其他文件锁相关错误

    Example:
        async with async_file_lock(Path("/tmp/my.lock"), timeout=10.0):
            # 执行需要互斥的操作
            pass
    """
    if timeout < 0:
        raise ValueError(f"timeout must be non-negative, got {timeout}")
    if poll_interval <= 0:
        raise ValueError(f"poll_interval must be positive, got {poll_interval}")

    loop = asyncio.get_running_loop()

    # 同步创建目录和打开文件（本地文件系统微秒级，避免 executor 取消导致 fd 泄漏）
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:
        raise FileLockError(f"Failed to create lock file {lock_path}: {e}") from e

    # 尝试获取锁（非阻塞模式 + 轮询）
    start_time = loop.time()
    acquired = False

    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as e:
                # EWOULDBLOCK 和 EAGAIN 表示锁被占用
                if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise FileLockError(f"Failed to acquire lock {lock_path}: {e}") from e

                # 检查超时
                elapsed = loop.time() - start_time
                if elapsed >= timeout:
                    raise FileLockTimeout(
                        f"Failed to acquire lock {lock_path} within {timeout}s"
                    )

                # 等待后重试
                await asyncio.sleep(poll_interval)

        # 锁已获取，执行用户代码
        yield

    finally:
        # 释放锁和关闭文件描述符
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as e:
                logger.warning(f"Failed to unlock {lock_path}: {e}")

        try:
            os.close(fd)
        except OSError as e:
            logger.warning(f"Failed to close lock file descriptor {lock_path}: {e}")
