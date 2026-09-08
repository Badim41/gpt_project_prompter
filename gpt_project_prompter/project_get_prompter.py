import os
from collections.abc import Sequence
from pathlib import Path

from network_tools import NetworkToolsAPI

from gpt_project_prompter_core.coordinator import (
    SEARCH_FILES_PROMPT_TEMPLATE,
    PromptWorkflowCoordinator,
)
from gpt_project_prompter_core.dispatcher import OutputDispatcher
from gpt_project_prompter_core.domain import (
    ProjectScanConfig,
    PromptGenerationResult,
)
from gpt_project_prompter_core.reader import SafeFileReader
from gpt_project_prompter_core.scanner import ProjectScanner
from gpt_project_prompter_core.storage import SQLiteCacheRepository

SEARCH_FILES_PROMPT: str = SEARCH_FILES_PROMPT_TEMPLATE


def get_project_structure(
    path: str | Path = ".",
    prefix: str = "",
    gpt_format: bool = True,
    base_path: str | Path | None = None,
    ignore_folders: Sequence[str] | None = None,
    ignore_file_list: Sequence[str] | None = None,
) -> str:
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
    )
    snapshot = scanner.scan(config)
    return snapshot.structure_text


def get_project_files_from_list(
    file_list: Sequence[str],
    base_path: str | Path = ".",
) -> str:
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
    model: str = "gemini-3-0-flash",
    print_file_list: bool = False,
    file_list: Sequence[str] | None = None,
    ignore_file_list: Sequence[str] | None = None,
) -> str:
    """
    Формирует полный промпт для LLM с двухуровневым кэшированием в SQLite и контролем вывода.

    :param network_tools: Объект для работы с API нейросети.
    :param path: Путь к корневой директории проекта. Если None, используется текущая рабочая директория (CWD).
    :param ignore_folders: Список имен папок, которые нужно исключить из сканирования.
    :param task: Описание задачи, которую должна выполнить нейросеть.
    :param model: Название модели нейросети для предварительного выбора файлов.
    :param print_file_list: Если True, выводит в консоль список отобранных файлов.
    :param file_list: Готовый список путей к файлам. Если передан, нейросеть не опрашивается.
    :param ignore_file_list: Список конкретных имен файлов, которые нужно игнорировать.
    :return: Отформатированная строка промпта со структурой, контентом файлов и текстом задачи.
    """
    resolved_path = Path(path).resolve() if path is not None else Path.cwd().resolve()
    folders_set = frozenset(ignore_folders) if ignore_folders else frozenset()
    files_set = frozenset(ignore_file_list) if ignore_file_list else frozenset()

    db_path = resolved_path / ".get_gpt_project" / "cache.db"

    scanner = ProjectScanner()
    cache_repo = SQLiteCacheRepository(db_path=db_path)
    file_reader = SafeFileReader()
    dispatcher = OutputDispatcher()

    coordinator = PromptWorkflowCoordinator(
        scanner=scanner,
        cache_repo=cache_repo,
        file_reader=file_reader,
        dispatcher=dispatcher,
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
