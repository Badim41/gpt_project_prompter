from __future__ import annotations

from collections.abc import Sequence
import logging
from pathlib import Path

from network_tools import NetworkToolsAPI

from gpt_project_prompter_core.constants import (
    DEFAULT_DETAIL_LEVEL,
    DEFAULT_MAX_FILES_IN_FOLDER,
    DEFAULT_MAX_FILES_IN_SUBTREE,
    DEFAULT_MAX_PROMPT_CHARS,
    DEFAULT_MAX_STRUCTURE_CHARS,
    DEFAULT_MODEL,
)
from gpt_project_prompter_core.coordinator import (
    SEARCH_FILES_PROMPT_TEMPLATE,
    PromptWorkflowCoordinator,
)
from gpt_project_prompter_core.dispatcher import OutputDispatcher
from gpt_project_prompter_core.domain import (
    ProjectScanConfig,
    PromptGenerationResult,
)
from gpt_project_prompter_core.edit_instructions import (
    EDIT_INSTRUCTIONS,
    get_edit_instructions,
)
from gpt_project_prompter_core.exceptions import CacheStorageError
from gpt_project_prompter_core.indexer import FileIndexer
from gpt_project_prompter_core.reader import SafeFileReader
from gpt_project_prompter_core.renderer import render_structure
from gpt_project_prompter_core.scanner import ProjectScanner
from gpt_project_prompter_core.storage import (
    SQLiteCacheRepository,
    SQLiteIndexCacheRepository,
)

logger = logging.getLogger(__name__)

SEARCH_FILES_PROMPT: str = SEARCH_FILES_PROMPT_TEMPLATE

__all__ = [
    "DEFAULT_MODEL",
    "EDIT_INSTRUCTIONS",
    "SEARCH_FILES_PROMPT",
    "get_edit_instructions",
    "get_gpt_prompt",
    "get_project_files_from_list",
    "get_project_structure",
]


def _validate_and_normalize_options(
    *,
    max_files_in_folder: int,
    max_files_in_subtree: int,
    detail_level: int,
    max_structure_chars: int,
    max_prompt_chars: int,
    expand_folders: Sequence[str],
    collapse_folders: Sequence[str],
) -> tuple[frozenset[str], frozenset[str]]:
    """Валидирует числовые параметры и нормализует относительные папки expand/collapse."""
    if max_files_in_folder <= 0:
        raise ValueError("max_files_in_folder должен быть больше нуля.")
    if max_files_in_subtree <= 0:
        raise ValueError("max_files_in_subtree должен быть больше нуля.")
    if max_structure_chars <= 0:
        raise ValueError("max_structure_chars должен быть больше нуля.")
    if max_prompt_chars <= 0:
        raise ValueError("max_prompt_chars должен быть больше нуля.")
    if not (0 <= detail_level <= 3):
        raise ValueError("detail_level должен быть в диапазоне от 0 до 3.")
    return (
        _normalize_folder_list(expand_folders, "expand_folders"),
        _normalize_folder_list(collapse_folders, "collapse_folders"),
    )


def _normalize_folder_list(folders: Sequence[str], param_name: str) -> frozenset[str]:
    result: set[str] = set()
    for raw in folders:
        norm = raw.strip().replace("\\", "/")
        while norm.startswith("./"):
            norm = norm[2:]
        norm = norm.rstrip("/")
        if not norm or norm.startswith("/") or ".." in norm or ":" in norm:
            raise ValueError(f"Некорректный относительный путь в {param_name}: {raw!r}")
        result.add(norm)
    return frozenset(result)


def get_project_structure(
    path: str | Path = ".",
    prefix: str = "",
    gpt_format: bool = True,
    base_path: str | Path | None = None,
    ignore_folders: Sequence[str] | None = None,
    ignore_file_list: Sequence[str] | None = None,
    *,
    max_files_in_folder: int = DEFAULT_MAX_FILES_IN_FOLDER,
    max_files_in_subtree: int = DEFAULT_MAX_FILES_IN_SUBTREE,
    detail_level: int = DEFAULT_DETAIL_LEVEL,
    max_structure_chars: int = DEFAULT_MAX_STRUCTURE_CHARS,
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
    use_git: bool = True,
    expand_folders: Sequence[str] = (),
    collapse_folders: Sequence[str] = (),
) -> str:
    """Возвращает структуру проекта (плоскую с индексом или дерево без повторного обхода)."""
    expand_set, collapse_set = _validate_and_normalize_options(
        max_files_in_folder=max_files_in_folder,
        max_files_in_subtree=max_files_in_subtree,
        detail_level=detail_level,
        max_structure_chars=max_structure_chars,
        max_prompt_chars=max_prompt_chars,
        expand_folders=expand_folders,
        collapse_folders=collapse_folders,
    )

    target_root = Path(path).resolve()
    base_root = Path(base_path).resolve() if base_path is not None else target_root
    folders_set = frozenset(ignore_folders) if ignore_folders else frozenset()
    files_set = frozenset(ignore_file_list) if ignore_file_list else frozenset()

    scanner = ProjectScanner()
    config = ProjectScanConfig(
        root_path=target_root,
        ignore_folders=folders_set,
        ignore_files=files_set,
        gpt_format=gpt_format,
        prefix=prefix,
        base_path=base_root,
        max_files_in_folder=max_files_in_folder,
        max_files_in_subtree=max_files_in_subtree,
        use_git=use_git,
        expand_folders=expand_set,
        collapse_folders=collapse_set,
    )
    snapshot = scanner.scan(config)
    if not gpt_format:
        return snapshot.structure_text

    if detail_level == 0:
        rendered = render_structure(
            snapshot,
            {},
            detail_level=0,
            max_structure_chars=max_structure_chars,
            expand_folders=expand_set,
        )
        return rendered.text

    index_cache: SQLiteIndexCacheRepository | None = None
    try:
        index_cache = SQLiteIndexCacheRepository(target_root / ".get_gpt_project" / "cache.db")
    except CacheStorageError as exc:
        logger.debug("Индексный кэш недоступен: %s", exc)

    indexer = FileIndexer(index_cache=index_cache)
    index = indexer.index_files(base_root, snapshot.canonical_relative_paths)
    rendered = render_structure(
        snapshot,
        index,
        detail_level=detail_level,
        max_structure_chars=max_structure_chars,
        expand_folders=expand_set,
    )
    return rendered.text


def get_project_files_from_list(
    file_list: Sequence[str],
    base_path: str | Path = ".",
) -> str:
    """Читает перечисленные файлы относительно base_path в текстовый блок."""
    target_root = Path(base_path).resolve()
    reader = SafeFileReader()
    return reader.read_files_content(
        root_path=target_root,
        relative_paths=file_list,
    )


def get_gpt_prompt(
    network_tools: NetworkToolsAPI,
    path: str | Path | None = None,
    ignore_folders: Sequence[str] | None = None,
    task: str = "Выведи все файлы из проекта",
    model: str = DEFAULT_MODEL,
    print_file_list: bool = False,
    file_list: Sequence[str] | None = None,
    ignore_file_list: Sequence[str] | None = None,
    *,
    max_files_in_folder: int = DEFAULT_MAX_FILES_IN_FOLDER,
    max_files_in_subtree: int = DEFAULT_MAX_FILES_IN_SUBTREE,
    detail_level: int = DEFAULT_DETAIL_LEVEL,
    max_structure_chars: int = DEFAULT_MAX_STRUCTURE_CHARS,
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
    use_git: bool = True,
    expand_folders: Sequence[str] = (),
    collapse_folders: Sequence[str] = (),
    include_edit_instructions: bool = False,
) -> str:
    """Формирует полный промпт для LLM с индексом файлов, сворачиванием папок и кэшем SQLite."""
    expand_set, collapse_set = _validate_and_normalize_options(
        max_files_in_folder=max_files_in_folder,
        max_files_in_subtree=max_files_in_subtree,
        detail_level=detail_level,
        max_structure_chars=max_structure_chars,
        max_prompt_chars=max_prompt_chars,
        expand_folders=expand_folders,
        collapse_folders=collapse_folders,
    )

    resolved_path = Path(path).resolve() if path is not None else Path.cwd().resolve()
    folders_set = frozenset(ignore_folders) if ignore_folders else frozenset()
    files_set = frozenset(ignore_file_list) if ignore_file_list else frozenset()

    db_path = resolved_path / ".get_gpt_project" / "cache.db"

    scanner = ProjectScanner()
    cache_repo = SQLiteCacheRepository(db_path=db_path)
    index_cache = SQLiteIndexCacheRepository(db_path=db_path)
    indexer = FileIndexer(index_cache=index_cache)
    file_reader = SafeFileReader()
    dispatcher = OutputDispatcher()

    coordinator = PromptWorkflowCoordinator(
        scanner=scanner,
        cache_repo=cache_repo,
        file_reader=file_reader,
        dispatcher=dispatcher,
        indexer=indexer,
    )

    result: PromptGenerationResult = coordinator.run(
        network_tools=network_tools,
        root_path=resolved_path,
        task=task,
        model=model,
        ignore_folders=folders_set,
        ignore_files=files_set,
        print_file_list=print_file_list,
        explicit_file_list=file_list,
        max_files_in_folder=max_files_in_folder,
        max_files_in_subtree=max_files_in_subtree,
        detail_level=detail_level,
        max_structure_chars=max_structure_chars,
        max_prompt_chars=max_prompt_chars,
        use_git=use_git,
        expand_folders=expand_set,
        collapse_folders=collapse_set,
        include_edit_instructions=include_edit_instructions,
    )

    return result.prompt_text


"""
if __name__ == "__main__":
    import secret
    from network_tools import NetworkToolsAPI
    from gpt_project_prompter import get_gpt_prompt

    network_api = NetworkToolsAPI(secret.network_tools_api)
    project_path = "C:/Users/as280/PycharmProjects/minecraft-ai"
    ignore_folders = ["node_modules"]
    task = "Перевести все запросы к ChatGPT на русский"

    prompt_text = get_gpt_prompt(network_api, project_path, ignore_folders, task)
    print(prompt_text)
"""
