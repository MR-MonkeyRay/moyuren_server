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
    """表示每日英语单词的展示信息。

    Attributes:
        word: 单词原文。
        phonetic: 音标；词库缺失时为 None。
        translation: 中文释义摘要。
        definition: 英文释义摘要；词库缺失时为 None。
        collins: 柯林斯星级或难度值；无法解析时为 None。
        oxford: 是否为牛津核心词。
        tag: 词库标签列表。
    """

    word: str
    phonetic: str | None
    translation: str
    definition: str | None
    collins: int | None
    oxford: bool
    tag: list[str]


class DictBackend(Protocol):
    """每日英语词典后端协议。

    实现类负责准备词库、按词查询并释放资源。
    """

    async def ensure_ready(self) -> None:
        """确保词典后端可用于查询。

        Raises:
            Exception: 后端准备失败时由具体实现抛出。
        """
        ...

    async def lookup(self, word: str) -> WordInfo | None:
        """查询单词信息。

        Args:
            word: 待查询的单词。

        Returns:
            查询到的单词信息；不存在或查询失败时为 None。
        """
        ...

    async def close(self) -> None:
        """关闭后端并释放资源。"""
        ...


_ensure_ready_async_lock: asyncio.Lock | None = None


def _get_ensure_ready_lock() -> asyncio.Lock:
    """获取进程内共享的词库准备异步锁。

    Returns:
        用于串行化 ensure_ready 流程的 asyncio.Lock。

    Side Effects:
        首次调用时会创建模块级锁对象。
    """
    global _ensure_ready_async_lock
    if _ensure_ready_async_lock is None:
        _ensure_ready_async_lock = asyncio.Lock()
    return _ensure_ready_async_lock


def _split_lines(value: str | None, max_lines: int) -> str | None:
    """清理多行文本并限制返回行数。

    Args:
        value: 原始文本；None 会直接返回 None。
        max_lines: 最多保留的非空行数。

    Returns:
        清理后的文本；无有效内容时返回 None。
    """
    if value is None:
        return None
    lines = [line.rstrip() for line in value.splitlines()]
    lines = [line for line in lines if line.strip()]
    if not lines:
        return None
    return "\n".join(lines[:max_lines])


def _parse_int(value: Any) -> int | None:
    """将词库字段解析为整数。

    Args:
        value: 待解析的任意字段值。

    Returns:
        解析后的整数；空值、布尔值或非法字符串返回 None。
    """
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
    """解析词库中的牛津核心词标记。

    Args:
        value: 词库中的原始标记值。

    Returns:
        True 表示标记为牛津核心词，否则为 False。
    """
    return value in (True, 1, "1")


def _safe_path_from_archive_name(name: str) -> PurePosixPath | None:
    """校验归档成员路径是否安全。

    Args:
        name: 归档中的成员路径。

    Returns:
        安全的相对 POSIX 路径；绝对路径、空路径片段或路径穿越返回 None。
    """
    path = PurePosixPath(name)
    if path.is_absolute():
        return None
    if any(part in ("..", "") for part in path.parts):
        return None
    return path


def _sha256sum(path: Path) -> str:
    """计算文件的 SHA-256 摘要。

    Args:
        path: 待计算摘要的文件路径。

    Returns:
        十六进制 SHA-256 字符串。

    Raises:
        OSError: 文件读取失败时抛出。
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_stardict_db_from_zip(
    archive_path: Path, out_path: Path, max_size_bytes: int
) -> int:
    """从 zip 归档中提取 stardict.db。

    Args:
        archive_path: zip 归档路径。
        out_path: 提取出的数据库目标路径。
        max_size_bytes: 允许的最大数据库大小。

    Returns:
        提取出的数据库文件大小，单位字节。

    Raises:
        FileNotFoundError: 归档中没有安全的 stardict.db 成员。
        ValueError: 成员声明大小或实际大小超过限制。
        zipfile.BadZipFile: 归档不是有效 zip 文件时抛出。

    Side Effects:
        创建目标目录并写入 out_path。
    """
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
    """从 7z 归档中提取 stardict.db。

    Args:
        archive_path: 7z 归档路径。
        out_path: 提取出的数据库目标路径。
        max_size_bytes: 允许的最大数据库大小。

    Returns:
        提取出的数据库文件大小，单位字节。

    Raises:
        FileNotFoundError: 归档中没有 stardict.db 或提取失败。
        ValueError: 成员声明大小或实际大小超过限制。
        Exception: py7zr 打开或提取归档失败时透传。

    Side Effects:
        创建临时目录、写入 out_path，并在退出时清理临时目录。
    """
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
    """基于本地 SQLite ECDICT 词库的每日英语后端。

    该后端在词库不存在时下载并解压归档，然后以只读方式查询 stardict 表。
    """

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
        """初始化 SQLite 词库后端。

        Args:
            db_path: 本地 SQLite 数据库路径。
            download_url: ECDICT 归档下载地址。
            checksum_sha256: 可选的归档 SHA-256 校验值。
            ghproxy_urls: 下载代理前缀列表。
            logger: 日志记录器。
        """
        self._db_path = Path(db_path)
        self._download_url = download_url
        self._checksum_sha256 = checksum_sha256.strip()
        self._ghproxy_urls = ghproxy_urls
        self._logger = logger
        self._last_failure_ts: float = 0.0

    def _build_download_urls(self) -> list[str]:
        """构建带代理回退的下载地址列表。

        Returns:
            先代理后原始地址的下载 URL 列表。
        """
        urls: list[str] = []
        for prefix in self._ghproxy_urls:
            p = prefix.strip() if isinstance(prefix, str) else ""
            if not p or not p.startswith(("http://", "https://")):
                continue
            urls.append(p.rstrip("/") + "/" + self._download_url)
        urls.append(self._download_url)
        return urls

    def _archive_ext(self) -> str:
        """从下载地址判断归档扩展名。

        Returns:
            支持的扩展名 .zip 或 .7z；不支持时返回空字符串。
        """
        path = urlparse(self._download_url).path.lower()
        if path.endswith(".zip"):
            return ".zip"
        if path.endswith(".7z"):
            return ".7z"
        return ""

    async def _download_to_tempfile(self, url: str, suffix: str) -> Path:
        """下载归档到数据库目录下的临时文件。

        Args:
            url: 实际下载地址。
            suffix: 临时文件后缀。

        Returns:
            下载完成的临时文件路径。

        Raises:
            httpx.HTTPError: 网络请求或 HTTP 状态失败时抛出。
            ValueError: 响应声明大小或实际下载大小超过限制。
            OSError: 临时文件创建或写入失败时抛出。

        Side Effects:
            创建数据库目录和临时文件；失败时会尝试删除临时文件。
        """
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
        """确保 SQLite 词库文件存在且可供后续查询。

        词库缺失时会串行化下载、校验和解压流程，并在失败后进入短暂冷却期。

        Raises:
            RuntimeError: 后端处于冷却期或所有下载地址均失败。
            ValueError: 归档类型、校验和或文件大小不符合要求。
            FileNotFoundError: 归档中缺少 stardict.db。
            Exception: 下载、解压或文件替换过程中的其他错误会透传。

        Side Effects:
            可能下载归档、写入数据库文件、创建锁文件，并记录成功或失败日志。
        """
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
        """同步查询 SQLite 词库中的单词信息。

        Args:
            word: 已清理的查询单词。

        Returns:
            查询到的 WordInfo；数据库不存在或未命中时返回 None。

        Raises:
            sqlite3.Error: 打开或查询数据库失败时抛出。
        """
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
        """异步查询单词信息。

        Args:
            word: 待查询单词，前后空白会被忽略。

        Returns:
            查询到的单词信息；输入为空、未命中或查询失败时返回 None。

        Side Effects:
            查询异常会写入 warning 日志。
        """
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
        """同步按难度范围从词库中伪随机选择单词。

        Args:
            seed: 随机种子，用于保证同一天回退选择稳定。
            difficulty_range: 柯林斯难度范围，顺序可颠倒。

        Returns:
            选中的单词；数据库不存在或没有匹配词时返回 None。

        Raises:
            sqlite3.Error: 打开或查询数据库失败时抛出。
        """
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
        """异步按难度范围选择回退单词。

        Args:
            seed: 随机种子。
            difficulty_range: 柯林斯难度范围。

        Returns:
            选中的单词；选择失败时返回 None。

        Side Effects:
            查询异常会写入 warning 日志。
        """
        try:
            return await asyncio.to_thread(
                self._sync_pick_random, seed, difficulty_range
            )
        except Exception as e:
            self._logger.warning("SQLite pick_random failed: %s", e)
            return None

    async def close(self) -> None:
        """关闭 SQLite 后端。

        当前实现按查询创建短连接，因此无需显式释放资源。
        """
        return


def build_dict_backend(
    cfg: DictBackendConfig,
    ghproxy_urls: list[str],
    logger: logging.Logger,
) -> DictBackend:
    """根据配置创建词典后端。

    Args:
        cfg: 词典后端配置。
        ghproxy_urls: 可选下载代理前缀列表。
        logger: 日志记录器。

    Returns:
        符合 DictBackend 协议的后端实例。

    Raises:
        NotImplementedError: 配置类型尚未实现时抛出。
    """
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
    """每日英语单词获取服务。

    服务优先从随机单词 API 获取候选词，再用本地词典补全释义；API 连续失败后回退到本地词库随机选择。
    """

    def __init__(
        self,
        config: DailyEnglishSource,
        backend: DictBackend,
        logger: logging.Logger,
    ) -> None:
        """初始化每日英语服务。

        Args:
            config: 每日英语数据源配置。
            backend: 单词释义查询后端。
            logger: 日志记录器。
        """
        self._config = config
        self._backend = backend
        self._logger = logger

    async def ensure_ready(self) -> None:
        """Ensure the dictionary backend is ready (e.g., download DB if needed)."""
        await self._backend.ensure_ready()

    def _difficulty_range_tuple(self) -> tuple[int, int]:
        """获取规范化后的单词难度范围。

        Returns:
            二元组形式的最小、最大难度；配置非法时返回默认范围 (3, 5)。
        """
        dr = self._config.difficulty_range
        if len(dr) != 2:
            return (3, 5)
        a, b = dr[0], dr[1]
        return (min(a, b), max(a, b))

    async def _fetch_word_from_api(
        self, client: httpx.AsyncClient, difficulty: int
    ) -> str | None:
        """从随机单词 API 获取一个指定难度的候选词。

        Args:
            client: 复用的异步 HTTP 客户端。
            difficulty: 请求的单词难度。

        Returns:
            API 返回的非空单词；响应格式异常或请求失败时返回 None。

        Side Effects:
            请求异常会写入 warning 日志。
        """
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
        """获取当天展示用单词信息。

        Returns:
            成功补全释义的单词信息；API 与本地回退均不可用时返回 None。

        Side Effects:
            会访问外部随机单词 API，并可能调用后端本地随机选择逻辑。
        """
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
    """带每日缓存的英语单词服务。

    该服务将 DailyEnglishService 的结果接入 DailyCache，失败时由缓存层负责降级。
    """

    def __init__(
        self,
        config: DailyEnglishSource,
        backend: DictBackend,
        logger: logging.Logger,
        cache_dir: Path,
    ) -> None:
        """初始化带缓存的每日英语服务。

        Args:
            config: 每日英语数据源配置。
            backend: 单词释义查询后端。
            logger: 日志记录器。
            cache_dir: 缓存目录。
        """
        super().__init__("daily_english", cache_dir, logger)
        self._service = DailyEnglishService(
            config=config, backend=backend, logger=logger
        )
        self._backend = backend

    async def fetch_fresh(self) -> dict | None:
        """获取未缓存的每日英语数据。

        Returns:
            可序列化的单词信息字典；无法获取时返回 None。

        Side Effects:
            会尝试准备词典后端，失败仅记录日志并继续走服务降级逻辑。
        """
        try:
            await self._backend.ensure_ready()
        except Exception as e:
            self.logger.warning("Dict backend not ready: %s", e)
        result = await self._service.fetch_daily_word()
        return dict(result) if result else None

    async def close(self) -> None:
        """关闭缓存服务持有的词典后端。"""
        await self._backend.close()
