from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from gpt_project_prompter_core.constants import (
    DEFAULT_MAX_FILES_IN_FOLDER,
    DEFAULT_MAX_FILES_IN_SUBTREE,
    MAX_TEXT_READ_BYTES,
)


class CollapseReason(str, Enum):
    """Причина сворачивания папки в структуре проекта."""

    VENV = "виртуальное окружение"
    DEPENDENCIES = "зависимости"
    NESTED_GIT = "вложенный git-репозиторий"
    TOO_MANY_FILES = "много файлов в папке"
    TOO_MANY_SUBTREE_FILES = "много файлов в поддереве"
    CONFIGURED = "по настройкам"
    BUDGET_LIMIT = "не помещается в лимит"


class FileReadStatus(str, Enum):
    """Статус безопасного чтения одного файла проекта."""

    OK = "ok"
    NOT_FOUND = "not_found"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    DECODE_ERROR = "decode_error"
    IO_ERROR = "io_error"


@dataclass(frozen=True, slots=True)
class CollapsedFolder:
    """Описание свёрнутой папки и её сводной статистики."""

    rel_path: str
    reason: CollapseReason
    file_count: int | None = None
    is_lower_bound: bool = False
    top_extensions: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectScanConfig:
    """Параметры сканирования структуры проекта."""

    root_path: Path
    ignore_folders: frozenset[str]
    ignore_files: frozenset[str]
    max_file_size_bytes: int = MAX_TEXT_READ_BYTES
    gpt_format: bool = True
    prefix: str = ""
    base_path: Path | None = None
    max_files_in_folder: int = DEFAULT_MAX_FILES_IN_FOLDER
    max_files_in_subtree: int = DEFAULT_MAX_FILES_IN_SUBTREE
    use_git: bool = True
    expand_folders: frozenset[str] = frozenset()
    collapse_folders: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ProjectStructureSnapshot:
    """Снимок структуры проекта: allowlist файлов, свёрнутые папки и хэш."""

    structure_text: str
    structure_hash: str
    canonical_relative_paths: tuple[str, ...]
    collapsed_folders: tuple[CollapsedFolder, ...] = ()


@dataclass(frozen=True, slots=True)
class CacheRecord:
    """Запись кэша выбора файлов для задачи и модели."""

    cache_key: str
    task: str
    model: str
    structure_hash: str
    structure_text: str
    selected_files: tuple[str, ...]
    created_at: int


@dataclass(frozen=True, slots=True)
class FileIndexRecord:
    """Запись кэша индекса (факты и контур) одного файла."""

    rel_path: str
    mtime_ns: int
    size: int
    index_version: int
    facts_json: str
    outline_json: str | None


@dataclass(frozen=True, slots=True)
class FileReadResult:
    """Результат чтения одного файла через SafeFileReader."""

    status: FileReadStatus
    text: str


@dataclass(frozen=True, slots=True)
class SelectedFile:
    """Один выбранный файл с признаком происхождения из маски."""

    path: str
    from_pattern: bool


@dataclass(frozen=True, slots=True)
class TruncatedPattern:
    """Информация о маске, превысившей лимит числа файлов."""

    pattern: str
    shown: int
    total: int


@dataclass(frozen=True, slots=True)
class Selection:
    """Результат раскрытия ответа LLM или списка файлов по allowlist."""

    files: tuple[SelectedFile, ...]
    skipped: tuple[str, ...]
    truncated_patterns: tuple[TruncatedPattern, ...]


@dataclass(frozen=True, slots=True)
class RenderedStructure:
    """Результат рендеринга структуры с учётом бюджета символов."""

    text: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PromptGenerationResult:
    """Итоговый результат генерации промпта проекта."""

    prompt_text: str
    is_cached: bool
    written_to_file: bool
    output_file_path: Path | None
    character_count: int
    skipped_paths: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
