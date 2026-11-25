from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from convert_gpt_answer import convert_answer_to_json
from network_tools import NetworkToolsAPI

from gpt_project_prompter_core.constants import (
    DEFAULT_DETAIL_LEVEL,
    DEFAULT_MAX_FILES_IN_FOLDER,
    DEFAULT_MAX_FILES_IN_SUBTREE,
    DEFAULT_MAX_PROMPT_CHARS,
    DEFAULT_MAX_STRUCTURE_CHARS,
)
from gpt_project_prompter_core.domain import (
    ProjectScanConfig,
    ProjectStructureSnapshot,
    PromptGenerationResult,
)
from gpt_project_prompter_core.indexer import FileIndexer
from gpt_project_prompter_core.interfaces import (
    CacheRepositoryProtocol,
    FileIndexerProtocol,
    OutputDispatcherProtocol,
    ProjectScannerProtocol,
    SafeFileReaderProtocol,
)
from gpt_project_prompter_core.prompt_builder import build_final_prompt
from gpt_project_prompter_core.renderer import render_structure
from gpt_project_prompter_core.selection import resolve_selection

SEARCH_FILES_PROMPT_TEMPLATE = """# Задача

Выведи пути к файлам, которые могут понадобиться для данной задачи:
{task}

# Формат ответа

Строго List[str] (json)

## Примеры ответа

["path/to/file_1","path/to/file_2"]

Можно указывать маски: `dir/*` (файлы папки без вложенных), `dir/**` (все файлы папки рекурсивно).
Папки с пометкой [свернуто] недоступны для масок. Перечисляй файлы поштучно, а маски используй для целых папок.

# Как читать структуру
Строка файла: путь, вес, строки, размер изображения или длительность. Строки с отступом ниже: классы, функции, методы, таблицы, схема JSON.

# Структура проекта
"""


class PromptWorkflowCoordinator:
    """Координирует сканирование, индексацию, выбор файлов LLM и сборку промпта."""

    def __init__(
        self,
        scanner: ProjectScannerProtocol,
        cache_repo: CacheRepositoryProtocol,
        file_reader: SafeFileReaderProtocol,
        dispatcher: OutputDispatcherProtocol,
        indexer: FileIndexerProtocol | None = None,
    ) -> None:
        self._scanner = scanner
        self._cache_repo = cache_repo
        self._file_reader = file_reader
        self._dispatcher = dispatcher
        self._indexer: FileIndexerProtocol = indexer or FileIndexer()

    def run(
        self,
        network_tools: NetworkToolsAPI,
        root_path: Path,
        task: str,
        model: str,
        ignore_folders: frozenset[str],
        ignore_files: frozenset[str],
        print_file_list: bool = False,
        explicit_file_list: Sequence[str] | None = None,
        *,
        max_files_in_folder: int = DEFAULT_MAX_FILES_IN_FOLDER,
        max_files_in_subtree: int = DEFAULT_MAX_FILES_IN_SUBTREE,
        detail_level: int = DEFAULT_DETAIL_LEVEL,
        max_structure_chars: int = DEFAULT_MAX_STRUCTURE_CHARS,
        max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
        use_git: bool = True,
        expand_folders: frozenset[str] = frozenset(),
        collapse_folders: frozenset[str] = frozenset(),
        include_edit_instructions: bool = False,
    ) -> PromptGenerationResult:
        """Выполняет полный цикл генерации промпта для проекта."""
        root_resolved = root_path.resolve()
        scan_config = ProjectScanConfig(
            root_path=root_resolved,
            ignore_folders=ignore_folders,
            ignore_files=ignore_files,
            max_files_in_folder=max_files_in_folder,
            max_files_in_subtree=max_files_in_subtree,
            use_git=use_git,
            expand_folders=expand_folders,
            collapse_folders=collapse_folders,
        )

        snapshot = self._scanner.scan(scan_config)
        index = self._indexer.index_files(root_resolved, snapshot.canonical_relative_paths)
        warnings: list[str] = []

        if explicit_file_list is not None:
            raw_selection = list(explicit_file_list)
            is_cache_hit = False
            selection = resolve_selection(
                raw_selection,
                snapshot.canonical_relative_paths,
                is_explicit_user_list=True,
            )
        else:
            raw_selection, is_cache_hit, llm_warnings = self._obtain_llm_selection(
                network_tools=network_tools,
                snapshot=snapshot,
                index=index,
                task=task,
                model=model,
                detail_level=detail_level,
                max_structure_chars=max_structure_chars,
                expand_folders=expand_folders,
                print_file_list=print_file_list,
            )
            warnings.extend(llm_warnings)
            selection = resolve_selection(
                raw_selection,
                snapshot.canonical_relative_paths,
                is_explicit_user_list=False,
            )

        if print_file_list:
            print("file_list =", [item.path for item in selection.files])
            if selection.skipped:
                print("skipped_paths =", list(selection.skipped))

        built = build_final_prompt(
            root_path=root_resolved,
            snapshot=snapshot,
            index=index,
            selection=selection,
            file_reader=self._file_reader,
            max_file_size_bytes=scan_config.max_file_size_bytes,
            max_structure_chars=max_structure_chars,
            max_prompt_chars=max_prompt_chars,
            expand_folders=expand_folders,
            include_edit_instructions=include_edit_instructions,
        )
        warnings.extend(built.warnings)

        dispatch_result = self._dispatcher.dispatch(
            content=built.text,
            output_dir=root_resolved,
        )

        return PromptGenerationResult(
            prompt_text=dispatch_result.prompt_text,
            is_cached=is_cache_hit,
            written_to_file=dispatch_result.written_to_file,
            output_file_path=dispatch_result.output_file_path,
            character_count=dispatch_result.character_count,
            skipped_paths=selection.skipped,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def _obtain_llm_selection(
        self,
        *,
        network_tools: NetworkToolsAPI,
        snapshot: ProjectStructureSnapshot,
        index: dict[str, Any],
        task: str,
        model: str,
        detail_level: int,
        max_structure_chars: int,
        expand_folders: frozenset[str],
        print_file_list: bool,
    ) -> tuple[list[str], bool, list[str]]:
        warnings: list[str] = []
        cached = self._cache_repo.get_selection(
            task=task,
            model=model,
            structure_hash=snapshot.structure_hash,
        )
        if cached is not None:
            if print_file_list:
                print("[PromptEngine] Использован кэш списка файлов из SQLite.")
            return list(cached), True, warnings

        rendered = render_structure(
            snapshot,
            index,
            detail_level=detail_level,
            max_structure_chars=max_structure_chars,
            expand_folders=expand_folders,
        )
        warnings.extend(rendered.warnings)

        search_prompt = SEARCH_FILES_PROMPT_TEMPLATE.format(task=task) + rendered.text
        parsed = self._query_and_parse_with_retry(network_tools, search_prompt, model)
        if parsed is None:
            warnings.append(
                "Не удалось разобрать список файлов из ответа LLM после повторного запроса; "
                "результат не кэширован."
            )
            return [], False, warnings

        self._cache_repo.store_selection(
            task=task,
            model=model,
            structure_hash=snapshot.structure_hash,
            structure_text=rendered.text,
            selected_files=parsed,
        )
        return parsed, False, warnings

    def _query_and_parse_with_retry(
        self,
        network_tools: NetworkToolsAPI,
        search_prompt: str,
        model: str,
    ) -> list[str] | None:
        for _ in range(2):
            response = network_tools.chatgpt_api(prompt=search_prompt, model=model)
            parsed = _parse_llm_file_list(response.response.text)
            if parsed:
                return parsed
        return None


def _parse_llm_file_list(raw_text: str) -> list[str] | None:
    normalized = raw_text.replace("\\\\", "/").replace("//", "/").replace("\\", "/")
    converted, parsed_files = convert_answer_to_json(
        normalized,
        keys=[],
        start_symbol="[",
        end_symbol="]",
    )
    if not converted or not isinstance(parsed_files, list):
        return None
    items = [str(item) for item in parsed_files if isinstance(item, str) and item.strip()]
    return items if items else None
