"""Atomic output files with the permissions of ordinary newly created files."""

import contextlib
import os
import tempfile
import threading
from pathlib import Path

from app.core.errors import StorageError


def _read_umask() -> int:
    """Read Linux's umask without changing process-global state.

    The portable fallback is used only once during import on the main thread.
    Writers use the cached mode, so worker threads never change the umask.
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
    """Apply the startup umask to an output before atomic replacement."""
    os.chmod(path, _OUTPUT_MODE)


def write_atomic(path: Path, content: str | bytes, *, fsync: bool = False) -> None:
    """Replace a target with UTF-8 text or bytes, cleaning up on any failure.

    The parent must exist. Filesystem errors propagate for callers to translate
    into their existing application errors or logging policy.
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
            # Preserve the original write/chmod/replace error if cleanup fails.
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
