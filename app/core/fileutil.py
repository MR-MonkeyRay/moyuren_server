"""原子输出文件，权限与普通新建文件一致。"""

import contextlib
import os
import tempfile
import threading
from pathlib import Path

from app.core.errors import StorageError


def _read_umask() -> int:
    """读取 Linux umask，且不修改进程全局状态。

    可移植的回退实现仅在导入时于主线程执行一次。
    写入方使用缓存的模式位，工作线程永远不会改动 umask。
    """
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii", errors="replace").splitlines():
            if line.startswith("Umask:"):
                return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    if threading.current_thread() is not threading.main_thread():
        raise StorageError("Cannot safely read the process umask outside the main thread")
    mask = os.umask(0)
    os.umask(mask)
    return mask


_OUTPUT_MODE = 0o666 & ~_read_umask()


def set_output_permissions(path: str | Path) -> None:
    """在原子替换前按启动时 umask 设置输出文件权限。"""
    os.chmod(path, _OUTPUT_MODE)


def write_atomic(path: Path, content: str | bytes, *, fsync: bool = False) -> None:
    """用 UTF-8 文本或字节原子替换目标文件，任何失败都会清理临时文件。

    父目录必须已存在。文件系统错误向上抛出，由调用方转换为
    各自的应用异常或按日志策略处理。
    """
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content.encode("utf-8") if isinstance(content, str) else content)
            handle.flush()
            set_output_permissions(temporary)
            if fsync:
                os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            # 清理失败时保留原始的 write/chmod/replace 错误。
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
