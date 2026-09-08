from pathlib import Path
from typing import Protocol, Sequence

from gpt_project_prompter_core.domain import (
    ProjectScanConfig,
    ProjectStructureSnapshot,
    PromptGenerationResult,
)


class CacheRepositoryProtocol(Protocol):
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


class ProjectScannerProtocol(Protocol):
    def scan(self, config: ProjectScanConfig) -> ProjectStructureSnapshot:
        ...


class SafeFileReaderProtocol(Protocol):
    def read_files_content(
        self,
        root_path: Path,
        relative_paths: Sequence[str],
        max_file_size_bytes: int,
    ) -> str:
        ...


class OutputDispatcherProtocol(Protocol):
    def dispatch(
        self,
        content: str,
        output_dir: Path,
        max_console_len: int = 2000,
    ) -> PromptGenerationResult:
        ...