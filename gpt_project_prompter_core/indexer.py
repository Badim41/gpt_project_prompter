from __future__ import annotations

from collections.abc import Sequence
import json
import logging
from pathlib import Path

from gpt_project_prompter_core.constants import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    INDEX_VERSION,
    MAX_TEXT_READ_BYTES,
    SQLITE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from gpt_project_prompter_core.domain import FileIndexRecord
from gpt_project_prompter_core.extractors.base import (
    FileFacts,
    FileIndexEntry,
    FileKind,
    SymbolExtractor,
    SymbolOutline,
)
from gpt_project_prompter_core.extractors.js_ts import JsTsExtractor
from gpt_project_prompter_core.extractors.json_signature import JsonSignatureExtractor
from gpt_project_prompter_core.extractors.python_ast import PythonAstExtractor
from gpt_project_prompter_core.extractors.sql_schema import (
    SqlSchemaExtractor,
    extract_sqlite_schema,
)
from gpt_project_prompter_core.interfaces import IndexCacheProtocol
from gpt_project_prompter_core.media_probe import MediaProbe

logger = logging.getLogger(__name__)


def default_extractors() -> tuple[SymbolExtractor, ...]:
    """Возвращает стандартный набор экстракторов символов."""
    return (
        PythonAstExtractor(),
        JsTsExtractor(),
        JsonSignatureExtractor(),
        SqlSchemaExtractor(),
    )


def discover_local_modules(allowlist: Sequence[str]) -> frozenset[str]:
    """Определяет имена локальных верхнеуровневых модулей и пакетов проекта."""
    modules: set[str] = set()
    for rel in allowlist:
        parts = rel.replace("\\", "/").split("/")
        if not parts:
            continue
        _add_module_from_parts(parts, modules)
        if parts[0] == "src" and len(parts) >= 2:
            _add_module_from_parts(parts[1:], modules)
    return frozenset(modules)


def _add_module_from_parts(parts: Sequence[str], out: set[str]) -> None:
    if len(parts) == 1:
        p = Path(parts[0])
        if p.suffix.lower() in (".py", ".pyi") and p.stem != "__init__":
            out.add(p.stem)
    else:
        out.add(parts[0])


class FileIndexer:
    """Индексирует файлы allowlist (факты + контур) с пакетным SQLite-кэшем."""

    def __init__(
        self,
        index_cache: IndexCacheProtocol | None = None,
        extractors: Sequence[SymbolExtractor] | None = None,
        media_probe: MediaProbe | None = None,
    ) -> None:
        self._cache = index_cache
        self._extractors: tuple[SymbolExtractor, ...] = (
            tuple(extractors) if extractors is not None else default_extractors()
        )
        self._media_probe = media_probe or MediaProbe()

    def index_files(
        self,
        root_path: Path,
        allowlist: Sequence[str],
    ) -> dict[str, FileIndexEntry]:
        """Возвращает индекс для всех файлов allowlist, переиспользуя актуальный кэш."""
        resolved_root = root_path.resolve()
        local_modules = discover_local_modules(allowlist)
        cached_map = self._cache.get_many(allowlist) if self._cache is not None else {}

        entries: dict[str, FileIndexEntry] = {}
        to_store: list[FileIndexRecord] = []

        for rel_path in allowlist:
            target = resolved_root / rel_path
            try:
                stat_res = target.stat()
            except OSError as exc:
                logger.debug("Не удалось получить stat для %s: %s", rel_path, exc)
                continue

            cached = cached_map.get(rel_path)
            entry = _try_from_cache(cached, stat_res.st_mtime_ns, stat_res.st_size)
            if entry is not None:
                entries[rel_path] = entry
                continue

            entry = self._index_single(target, rel_path, stat_res.st_size, local_modules)
            entries[rel_path] = entry
            to_store.append(
                FileIndexRecord(
                    rel_path=rel_path,
                    mtime_ns=stat_res.st_mtime_ns,
                    size=stat_res.st_size,
                    index_version=INDEX_VERSION,
                    facts_json=json.dumps(entry.facts.to_dict(), ensure_ascii=False),
                    outline_json=(
                        json.dumps(entry.outline.to_dict(), ensure_ascii=False)
                        if entry.outline is not None
                        else None
                    ),
                )
            )

        if self._cache is not None:
            if to_store:
                self._cache.put_many(to_store)
            self._cache.prune_except(allowlist)
        return entries

    def _index_single(
        self,
        target: Path,
        rel_path: str,
        size_bytes: int,
        local_modules: frozenset[str],
    ) -> FileIndexEntry:
        ext = target.suffix.lower()
        try:
            if ext in SQLITE_EXTENSIONS:
                facts = FileFacts(size_bytes=size_bytes, kind=FileKind.SQLITE)
                outline = extract_sqlite_schema(target)
                return FileIndexEntry(rel_path=rel_path, facts=facts, outline=outline)

            if ext in IMAGE_EXTENSIONS:
                probe = self._media_probe.probe_image(target)
                facts = FileFacts(
                    size_bytes=size_bytes,
                    kind=FileKind.IMAGE,
                    width=probe.width,
                    height=probe.height,
                )
                return FileIndexEntry(rel_path=rel_path, facts=facts)

            if ext in AUDIO_EXTENSIONS or ext in VIDEO_EXTENSIONS:
                kind = FileKind.VIDEO if ext in VIDEO_EXTENSIONS else FileKind.AUDIO
                probe = self._media_probe.probe_audio_video(target)
                facts = FileFacts(
                    size_bytes=size_bytes,
                    kind=kind,
                    width=probe.width if kind is FileKind.VIDEO else None,
                    height=probe.height if kind is FileKind.VIDEO else None,
                    duration_seconds=probe.duration_seconds,
                )
                return FileIndexEntry(rel_path=rel_path, facts=facts)

            return self._index_text_or_binary(target, rel_path, size_bytes, local_modules)
        except Exception as exc:
            logger.debug("Ошибка индексации %s: %s", rel_path, exc)
            return FileIndexEntry(
                rel_path=rel_path,
                facts=FileFacts(size_bytes=size_bytes, kind=FileKind.BINARY),
            )

    def _index_text_or_binary(
        self,
        target: Path,
        rel_path: str,
        size_bytes: int,
        local_modules: frozenset[str],
    ) -> FileIndexEntry:
        with target.open("rb") as fp:
            head = fp.read(1024)
            if b"\x00" in head:
                return FileIndexEntry(
                    rel_path=rel_path,
                    facts=FileFacts(size_bytes=size_bytes, kind=FileKind.BINARY),
                )
            if size_bytes > MAX_TEXT_READ_BYTES:
                return FileIndexEntry(
                    rel_path=rel_path,
                    facts=FileFacts(size_bytes=size_bytes, kind=FileKind.TEXT),
                )
            raw = head + fp.read()

        line_count = _count_lines(raw)
        facts = FileFacts(size_bytes=size_bytes, kind=FileKind.TEXT, line_count=line_count)
        outline = self._run_extractors(raw, rel_path, local_modules)
        return FileIndexEntry(rel_path=rel_path, facts=facts, outline=outline)

    def _run_extractors(
        self,
        raw: bytes,
        rel_path: str,
        local_modules: frozenset[str],
    ) -> SymbolOutline | None:
        for extractor in self._extractors:
            if not extractor.supports(rel_path):
                continue
            try:
                return extractor.extract(raw, rel_path, local_modules)
            except Exception as exc:
                logger.debug("Ошибка экстрактора для %s: %s", rel_path, exc)
                return None
        return None


def _count_lines(raw: bytes) -> int:
    if not raw:
        return 0
    return raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1)


def _try_from_cache(
    record: FileIndexRecord | None,
    mtime_ns: int,
    size: int,
) -> FileIndexEntry | None:
    if record is None:
        return None
    if (
        record.mtime_ns != mtime_ns
        or record.size != size
        or record.index_version != INDEX_VERSION
    ):
        return None
    try:
        facts_obj: object = json.loads(record.facts_json)
        if not isinstance(facts_obj, dict):
            return None
        facts = FileFacts.from_dict(facts_obj)
        outline: SymbolOutline | None = None
        if record.outline_json is not None:
            outline_obj: object = json.loads(record.outline_json)
            if isinstance(outline_obj, dict):
                outline = SymbolOutline.from_dict(outline_obj)
        return FileIndexEntry(rel_path=record.rel_path, facts=facts, outline=outline)
    except (json.JSONDecodeError, ValueError):
        return None
