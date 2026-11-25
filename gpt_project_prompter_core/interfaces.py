from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from gpt_project_prompter_core.domain import (
    FileIndexRecord,
    FileReadResult,
    ProjectScanConfig,
    ProjectStructureSnapshot,
    PromptGenerationResult,
)
from gpt_project_prompter_core.extractors.base import FileIndexEntry
from gpt_project_prompter_core.file_sources import FileSourceResult
from gpt_project_prompter_core.folder_policy import FolderPolicy
from gpt_project_prompter_core.media_probe import MediaProbeResult


class CacheRepositoryProtocol(Protocol):
    """Протокол репозитория кэша выбора файлов."""

    def initialize_schema(self) -> None:
        ...

    def get_selection(
        self,
        task: str,
        model: str,
        structure_hash: str,
    ) -> tuple[str, ...] | None:
        ...

    def store_selection(
        self,
        task: str,
        model: str,
        structure_hash: str,
        structure_text: str,
        selected_files: Sequence[str],
    ) -> None:
        ...


class IndexCacheProtocol(Protocol):
    """Протокол пакетного кэша индекса файлов (факты + контур)."""

    def get_many(self, rel_paths: Sequence[str]) -> dict[str, FileIndexRecord]:
        ...

    def put_many(self, records: Sequence[FileIndexRecord]) -> None:
        ...

    def prune_except(self, valid_rel_paths: Sequence[str]) -> None:
        ...


class FileSourceProtocol(Protocol):
    """Протокол источника файлов проекта (обход диска или git)."""

    def collect(
        self,
        config: ProjectScanConfig,
        policy: FolderPolicy,
    ) -> FileSourceResult | None:
        ...


class GitRunnerProtocol(Protocol):
    """Протокол запуска git ls-files для подмены в тестах."""

    def list_files(self, root_path: Path) -> Sequence[str] | None:
        ...


class FfprobeRunnerProtocol(Protocol):
    """Протокол запуска ffprobe для подмены в тестах."""

    def probe(self, path: Path) -> MediaProbeResult | None:
        ...


class FileIndexerProtocol(Protocol):
    """Протокол индексатора файлов allowlist."""

    def index_files(
        self,
        root_path: Path,
        allowlist: Sequence[str],
    ) -> dict[str, FileIndexEntry]:
        ...


class ProjectScannerProtocol(Protocol):
    """Протокол оркестратора сканирования структуры проекта."""

    def scan(self, config: ProjectScanConfig) -> ProjectStructureSnapshot:
        ...


class SafeFileReaderProtocol(Protocol):
    """Протокол безопасного чтения файлов проекта."""

    def read_one(
        self,
        root_path: Path,
        relative_path: str,
        max_file_size_bytes: int = 2_097_152,
    ) -> FileReadResult:
        ...

    def read_files_content(
        self,
        root_path: Path,
        relative_paths: Sequence[str],
        max_file_size_bytes: int = 2_097_152,
    ) -> str:
        ...


class OutputDispatcherProtocol(Protocol):
    """Протокол вывода и сохранения готового промпта."""

    def dispatch(
        self,
        content: str,
        output_dir: Path,
        max_console_len: int = 2000,
    ) -> PromptGenerationResult:
        ...
