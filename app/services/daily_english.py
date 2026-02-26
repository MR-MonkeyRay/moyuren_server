from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import random
import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, TypedDict
from urllib.parse import quote, urlparse

import httpx

from app.core.config import DailyEnglishSource, DictBackendConfig, SQLiteBackendConfig
from app.core.filelock import async_file_lock
from app.services.calendar import today_business
from app.services.daily_cache import DailyCache


class WordInfo(TypedDict):
    word: str
    phonetic: str | None
    translation: str
    definition: str | None
    collins: int | None
    oxford: bool
    tag: list[str]


class DictBackend(Protocol):
    async def ensure_ready(self) -> None: ...

    async def lookup(self, word: str) -> WordInfo | None: ...

    async def close(self) -> None: ...


_ensure_ready_async_lock: asyncio.Lock | None = None


def _get_ensure_ready_lock() -> asyncio.Lock:
    global _ensure_ready_async_lock
    if _ensure_ready_async_lock is None:
        _ensure_ready_async_lock = asyncio.Lock()
    return _ensure_ready_async_lock


def _split_lines(value: str | None, max_lines: int) -> str | None:
    if value is None:
        return None
    lines = [line.rstrip() for line in value.splitlines()]
    lines = [line for line in lines if line.strip()]
    if not lines:
        return None
    return "\n".join(lines[:max_lines])


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _parse_bool_oxford(value: Any) -> bool:
    return value in (True, 1, "1")


def _safe_path_from_archive_name(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    if path.is_absolute():
        return None
    if any(part in ("..", "") for part in path.parts):
        return None
    return path


def _sha256sum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_stardict_db_from_zip(
    archive_path: Path, out_path: Path, max_size_bytes: int
) -> int:
    with zipfile.ZipFile(archive_path) as zf:
        member: zipfile.ZipInfo | None = None
        for info in zf.infolist():
            if info.is_dir():
                continue
            safe_name = _safe_path_from_archive_name(info.filename)
            if safe_name is None:
                continue
            if safe_name.name != "stardict.db":
                continue
            member = info
            break

        if member is None:
            raise FileNotFoundError("stardict.db not found in zip archive")

        if member.file_size > max_size_bytes:
            raise ValueError(f"Extracted stardict.db too large: {member.file_size} bytes")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member, "r") as src, out_path.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)

    size = out_path.stat().st_size
    if size > max_size_bytes:
        raise ValueError(f"Extracted stardict.db too large: {size} bytes")
    return size


def _extract_stardict_db_from_7z(
    archive_path: Path, out_path: Path, max_size_bytes: int
) -> int:
    import py7zr

    with py7zr.SevenZipFile(archive_path, mode="r") as z:
        selected_name: str | None = None

        try:
            entries = z.list()
        except Exception:
            entries = None

        if entries is not None:
            for entry in entries:
                name = getattr(entry, "filename", None)
                if not isinstance(name, str):
                    continue
                safe_name = _safe_path_from_archive_name(name)
                if safe_name is None:
                    continue
                if safe_name.name != "stardict.db":
                    continue
                uncompressed = getattr(entry, "uncompressed", None)
                if isinstance(uncompressed, int) and uncompressed > max_size_bytes:
                    raise ValueError(
                        f"Extracted stardict.db too large: {uncompressed} bytes"
                    )
                selected_name = name
                break

        if selected_name is None:
            for name in z.getnames():
                safe_name = _safe_path_from_archive_name(name)
                if safe_name is None:
                    continue
                if safe_name.name == "stardict.db":
                    selected_name = name
                    break

        if selected_name is None:
            raise FileNotFoundError("stardict.db not found in 7z archive")

        with tempfile.TemporaryDirectory(prefix="daily_english_7z_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            z.extract(path=tmp_dir, targets=[selected_name])
            extracted_path = tmp_dir / Path(selected_name)
            if not extracted_path.exists():
                raise FileNotFoundError("stardict.db extraction failed")

            size = extracted_path.stat().st_size
            if size > max_size_bytes:
                raise ValueError(
                    f"Extracted stardict.db too large: {size} bytes"
                )

            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(extracted_path, out_path)

    return out_path.stat().st_size


class SQLiteBackend:
    _COOLDOWN_SEC = 30 * 60
    _MAX_DB_SIZE_BYTES = 1024 * 1024 * 1024  # 1 GB
    _MAX_DOWNLOAD_SIZE_BYTES = 1024 * 1024 * 1024  # 1GB archive limit

    def __init__(
        self,
        db_path: str,
        download_url: str,
        checksum_sha256: str,
        ghproxy_urls: list[str],
        logger: logging.Logger,
    ) -> None:
        self._db_path = Path(db_path)
        self._download_url = download_url
        self._checksum_sha256 = checksum_sha256.strip()
        self._ghproxy_urls = ghproxy_urls
        self._logger = logger
        self._last_failure_ts: float = 0.0

    def _build_download_urls(self) -> list[str]:
        urls: list[str] = []
        for prefix in self._ghproxy_urls:
            p = prefix.strip() if isinstance(prefix, str) else ""
            if not p or not p.startswith(("http://", "https://")):
                continue
            urls.append(p.rstrip("/") + "/" + self._download_url)
        urls.append(self._download_url)
        return urls

    def _archive_ext(self) -> str:
        path = urlparse(self._download_url).path.lower()
        if path.endswith(".zip"):
            return ".zip"
        if path.endswith(".7z"):
            return ".7z"
        return ""

    async def _download_to_tempfile(self, url: str, suffix: str) -> Path:
        timeout = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
        headers = {"User-Agent": "moyuren_server/1.0"}

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self._db_path.parent,
            suffix=suffix or ".tmp",
            prefix="daily_english_",
        )
        os.close(fd)
        tmp_path = Path(tmp_name)

        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, headers=headers
            ) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    content_length = resp.headers.get("content-length")
                    if content_length:
                        try:
                            cl = int(content_length)
                        except (ValueError, TypeError):
                            cl = 0
                        if cl > self._MAX_DOWNLOAD_SIZE_BYTES:
                            raise ValueError(
                                f"Archive too large: {content_length} bytes"
                            )
                    downloaded = 0
                    with tmp_path.open("wb") as out:
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                downloaded += len(chunk)
                                if downloaded > self._MAX_DOWNLOAD_SIZE_BYTES:
                                    raise ValueError(
                                        f"Archive download exceeded limit: {downloaded} bytes"
                                    )
                                out.write(chunk)
            return tmp_path
        except Exception:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            raise

    async def ensure_ready(self) -> None:
        if self._db_path.exists():
            return

        now = time.time()
        if self._last_failure_ts > 0 and (now - self._last_failure_ts) < self._COOLDOWN_SEC:
            remaining = int(self._COOLDOWN_SEC - (now - self._last_failure_ts))
            raise RuntimeError(
                f"SQLite backend is cooling down after failure ({remaining}s remaining)"
            )

        async_lock = _get_ensure_ready_lock()
        lock_file = Path(str(self._db_path) + ".lock")

        async with async_lock:
            async with async_file_lock(lock_file, timeout=300.0):
                if self._db_path.exists():
                    return

                start_ts = time.perf_counter()
                archive_path: Path | None = None

                try:
                    urls = self._build_download_urls()
                    suffix = self._archive_ext()
                    if not suffix:
                        raise ValueError(
                            "Unsupported archive type: expected .zip or .7z"
                        )

                    last_error: Exception | None = None
                    for url in urls:
                        try:
                            self._logger.info(
                                "Downloading ECDICT archive from %s", url
                            )
                            archive_path = await self._download_to_tempfile(
                                url, suffix=suffix
                            )
                            break
                        except Exception as e:
                            last_error = e
                            self._logger.warning(
                                "Download failed from %s: %s", url, e
                            )
                            continue

                    if archive_path is None:
                        raise RuntimeError(
                            f"All download URLs failed: {last_error}"
                        )

                    if self._checksum_sha256:
                        digest = await asyncio.to_thread(_sha256sum, archive_path)
                        if digest.lower() != self._checksum_sha256.lower():
                            raise ValueError("Archive checksum mismatch (sha256)")

                    fd, tmp_db_name = tempfile.mkstemp(
                        dir=self._db_path.parent,
                        suffix=".db.tmp",
                        prefix="stardict_",
                    )
                    os.close(fd)
                    tmp_db_path = Path(tmp_db_name)

                    try:
                        if suffix == ".zip":
                            extracted_size = await asyncio.to_thread(
                                _extract_stardict_db_from_zip,
                                archive_path,
                                tmp_db_path,
                                self._MAX_DB_SIZE_BYTES,
                            )
                        else:
                            extracted_size = await asyncio.to_thread(
                                _extract_stardict_db_from_7z,
                                archive_path,
                                tmp_db_path,
                                self._MAX_DB_SIZE_BYTES,
                            )

                        os.replace(tmp_db_path, self._db_path)
                    finally:
                        with contextlib.suppress(OSError):
                            tmp_db_path.unlink(missing_ok=True)

                    elapsed_ms = int((time.perf_counter() - start_ts) * 1000)
                    archive_size = archive_path.stat().st_size if archive_path.exists() else 0
                    self._logger.info(
                        "ECDICT sqlite ready: db=%s elapsed=%dms "
                        "archive=%dB extracted=%dB",
                        self._db_path,
                        elapsed_ms,
                        archive_size,
                        extracted_size,
                    )
                except Exception as e:
                    self._last_failure_ts = time.time()
                    self._logger.warning(
                        "Failed to ensure ECDICT sqlite backend ready: %s",
                        e,
                        exc_info=True,
                    )
                    raise
                finally:
                    if archive_path is not None:
                        with contextlib.suppress(OSError):
                            archive_path.unlink(missing_ok=True)

    def _sync_lookup(self, word: str) -> WordInfo | None:
        if not self._db_path.exists():
            return None

        import sqlite3

        db_uri_path = quote(self._db_path.resolve().as_posix(), safe="/")
        conn = sqlite3.connect(f"file:{db_uri_path}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT word, phonetic, translation, definition, "
                "collins, oxford, tag "
                "FROM stardict WHERE word = ? COLLATE NOCASE LIMIT 1",
                (word,),
            )
            row = cur.fetchone()
            if row is None:
                return None

            translation = _split_lines(
                str(row["translation"]) if row["translation"] else "", 3
            ) or ""
            definition = _split_lines(
                str(row["definition"]) if row["definition"] else None, 2
            )
            phonetic_raw = row["phonetic"]
            tags_raw = row["tag"]

            return {
                "word": str(row["word"])[:100],
                "phonetic": (
                    str(phonetic_raw)[:200]
                    if isinstance(phonetic_raw, str) and phonetic_raw.strip()
                    else None
                ),
                "translation": translation,
                "definition": definition,
                "collins": _parse_int(row["collins"]),
                "oxford": _parse_bool_oxford(row["oxford"]),
                "tag": [t[:30] for t in str(tags_raw).split()[:10] if t] if tags_raw else [],
            }
        finally:
            conn.close()

    async def lookup(self, word: str) -> WordInfo | None:
        w = word.strip()
        if not w:
            return None
        try:
            return await asyncio.to_thread(self._sync_lookup, w)
        except Exception as e:
            self._logger.warning("SQLite lookup failed for %s: %s", w, e)
            return None

    def _sync_pick_random(
        self, seed: int, difficulty_range: tuple[int, int]
    ) -> str | None:
        if not self._db_path.exists():
            return None

        import sqlite3

        min_diff, max_diff = difficulty_range
        if min_diff > max_diff:
            min_diff, max_diff = max_diff, min_diff

        db_uri_path = quote(self._db_path.resolve().as_posix(), safe="/")
        conn = sqlite3.connect(f"file:{db_uri_path}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            # 获取 rowid 范围，避免 OFFSET 线性退化
            cur.execute(
                "SELECT MIN(rowid), MAX(rowid) FROM stardict "
                "WHERE collins BETWEEN ? AND ?",
                (min_diff, max_diff),
            )
            row = cur.fetchone()
            if row is None or row[0] is None:
                return None
            min_rowid, max_rowid = row[0], row[1]

            rng = random.Random(seed)
            # 尝试最多 10 次随机 rowid 命中
            for _ in range(10):
                rand_rowid = rng.randint(min_rowid, max_rowid)
                cur.execute(
                    "SELECT word FROM stardict "
                    "WHERE rowid >= ? AND collins BETWEEN ? AND ? "
                    "LIMIT 1",
                    (rand_rowid, min_diff, max_diff),
                )
                picked = cur.fetchone()
                if picked:
                    return str(picked[0])
            return None
        finally:
            conn.close()

    async def pick_random(
        self, seed: int, difficulty_range: tuple[int, int]
    ) -> str | None:
        try:
            return await asyncio.to_thread(
                self._sync_pick_random, seed, difficulty_range
            )
        except Exception as e:
            self._logger.warning("SQLite pick_random failed: %s", e)
            return None

    async def close(self) -> None:
        return


def build_dict_backend(
    cfg: DictBackendConfig,
    ghproxy_urls: list[str],
    logger: logging.Logger,
) -> DictBackend:
    if isinstance(cfg, SQLiteBackendConfig):
        return SQLiteBackend(
            db_path=cfg.db_path,
            download_url=cfg.download_url,
            checksum_sha256=cfg.checksum_sha256,
            ghproxy_urls=ghproxy_urls,
            logger=logger,
        )
    raise NotImplementedError(f"{type(cfg).__name__} backend not implemented yet")


class DailyEnglishService:
    def __init__(
        self,
        config: DailyEnglishSource,
        backend: DictBackend,
        logger: logging.Logger,
    ) -> None:
        self._config = config
        self._backend = backend
        self._logger = logger

    async def ensure_ready(self) -> None:
        """Ensure the dictionary backend is ready (e.g., download DB if needed)."""
        await self._backend.ensure_ready()

    def _difficulty_range_tuple(self) -> tuple[int, int]:
        dr = self._config.difficulty_range
        if len(dr) != 2:
            return (3, 5)
        a, b = dr[0], dr[1]
        return (min(a, b), max(a, b))

    async def _fetch_word_from_api(
        self, client: httpx.AsyncClient, difficulty: int
    ) -> str | None:
        try:
            resp = await client.get(
                self._config.word_api_url,
                params={"number": 1, "diff": difficulty},
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                return None
            word = data[0]
            if not isinstance(word, str):
                return None
            return word.strip() or None
        except Exception as e:
            self._logger.warning("Random word API failed: %s", e)
            return None

    async def fetch_daily_word(self) -> WordInfo | None:
        timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
        api_failures = 0
        min_d, max_d = self._difficulty_range_tuple()

        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True
        ) as client:
            for attempt in range(self._config.max_retries):
                difficulty = random.randint(min_d, max_d)
                word = await self._fetch_word_from_api(client, difficulty)
                if word is None:
                    api_failures += 1
                    if api_failures >= self._config.api_failure_threshold:
                        break
                    continue

                info = await self._backend.lookup(word)
                if info is None:
                    continue

                return info

        # Level 2: only when API failed threshold+ times, fall back to local pick
        if api_failures >= self._config.api_failure_threshold:
            pick_random = getattr(self._backend, "pick_random", None)
            if callable(pick_random):
                seed = int(today_business().strftime("%Y%m%d"))
                word = await pick_random(
                    seed=seed, difficulty_range=self._difficulty_range_tuple()
                )
                if word:
                    info = await self._backend.lookup(word)
                    if info is not None:
                        return info

        # Level 3/4: return None, let DailyCache handle degradation
        return None


class CachedDailyEnglishService(DailyCache[dict]):
    def __init__(
        self,
        config: DailyEnglishSource,
        backend: DictBackend,
        logger: logging.Logger,
        cache_dir: Path,
    ) -> None:
        super().__init__("daily_english", cache_dir, logger)
        self._service = DailyEnglishService(
            config=config, backend=backend, logger=logger
        )
        self._backend = backend

    async def fetch_fresh(self) -> dict | None:
        try:
            await self._backend.ensure_ready()
        except Exception as e:
            self.logger.warning("Dict backend not ready: %s", e)
        result = await self._service.fetch_daily_word()
        return dict(result) if result else None

    async def close(self) -> None:
        await self._backend.close()
