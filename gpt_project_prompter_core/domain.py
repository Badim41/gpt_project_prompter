from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectScanConfig:
    root_path: Path
    ignore_folders: frozenset[str]
    ignore_files: frozenset[str]
    max_file_size_bytes: int = 2_097_152
    gpt_format: bool = True
    prefix: str = ""
    base_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ProjectStructureSnapshot:
    structure_text: str
    structure_hash: str
    canonical_relative_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CacheRecord:
    cache_key: str
    task: str
    model: str
    structure_hash: str
    structure_text: str
    selected_files: tuple[str, ...]
    created_at: int


@dataclass(frozen=True, slots=True)
class PromptGenerationResult:
    prompt_text: str
    is_cached: bool
    written_to_file: bool
    output_file_path: Path | None
    character_count: int
