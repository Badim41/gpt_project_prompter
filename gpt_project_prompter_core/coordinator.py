from pathlib import Path
from typing import Sequence

from convert_gpt_answer import convert_answer_to_json
from network_tools import NetworkToolsAPI

from gpt_project_prompter_core.domain import (
    ProjectScanConfig,
    PromptGenerationResult,
)
from gpt_project_prompter_core.interfaces import (
    CacheRepositoryProtocol,
    OutputDispatcherProtocol,
    ProjectScannerProtocol,
    SafeFileReaderProtocol,
)

SEARCH_FILES_PROMPT_TEMPLATE = """# Задача

Выведи пути к файлам, которые могут понадобиться для данной задачи:
{task}

# Формат ответа

Строго List[str] (json)

## Примеры ответа

["path/to/file_1","path/to/file_2"]

# Структура проекта
"""


class PromptWorkflowCoordinator:
    def __init__(
        self,
        scanner: ProjectScannerProtocol,
        cache_repo: CacheRepositoryProtocol,
        file_reader: SafeFileReaderProtocol,
        dispatcher: OutputDispatcherProtocol,
    ) -> None:
        self._scanner = scanner
        self._cache_repo = cache_repo
        self._file_reader = file_reader
        self._dispatcher = dispatcher

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
    ) -> PromptGenerationResult:
        root_resolved = root_path.resolve()
        scan_config = ProjectScanConfig(
            root_path=root_resolved,
            ignore_folders=ignore_folders,
            ignore_files=ignore_files,
        )

        snapshot = self._scanner.scan(scan_config)
        is_cache_hit = False

        if explicit_file_list is not None:
            selected_files = list(explicit_file_list)
        else:
            cached_selection = self._cache_repo.get_selection(
                task=task,
                model=model,
                structure_hash=snapshot.structure_hash,
            )

            if cached_selection is not None:
                selected_files = list(cached_selection)
                is_cache_hit = True
                if print_file_list:
                    print("[PromptEngine] Использован кэш списка файлов из SQLite.")
            else:
                search_prompt = (
                    SEARCH_FILES_PROMPT_TEMPLATE.format(task=task)
                    + snapshot.structure_text
                )
                response = network_tools.chatgpt_api(
                    prompt=search_prompt,
                    model=model,
                )
                response_raw = (
                    response.response.text.replace("\\\\", "/")
                    .replace("//", "/")
                    .replace("\\", "/")
                )
                converted, parsed_files = convert_answer_to_json(
                    response_raw,
                    keys=[],
                    start_symbol="[",
                    end_symbol="]",
                )

                selected_files = list(parsed_files) if (converted and isinstance(parsed_files, list)) else []

                self._cache_repo.store_selection(
                    task=task,
                    model=model,
                    structure_hash=snapshot.structure_hash,
                    structure_text=snapshot.structure_text,
                    selected_files=selected_files,
                )

        if print_file_list:
            print("file_list =", selected_files)

        files_content = self._file_reader.read_files_content(
            root_path=root_resolved,
            relative_paths=selected_files,
            max_file_size_bytes=scan_config.max_file_size_bytes,
        )

        root_folder_name = root_resolved.name
        final_prompt_text = (
            f"# Структура проекта {root_folder_name}\n{snapshot.structure_text}\n\n"
            f"# Файлы\n{files_content}\n\n"
            f"# Запрос\n\n"
        )

        dispatch_result = self._dispatcher.dispatch(
            content=final_prompt_text,
            output_dir=root_resolved,
        )

        return PromptGenerationResult(
            prompt_text=dispatch_result.prompt_text,
            is_cached=is_cache_hit,
            written_to_file=dispatch_result.written_to_file,
            output_file_path=dispatch_result.output_file_path,
            character_count=dispatch_result.character_count,
        )