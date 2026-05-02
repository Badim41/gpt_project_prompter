import json
import os
import re
import inspect

from convert_gpt_answer import convert_answer_to_json
from network_tools import NetworkToolsAPI

# https://github.com/Badim41/convert_gpt_answer

SEARCH_FILES_PROMPT = """
# Задача

Выведи пути к файлам, которые могут понадобиться для данной задачи:
{}

# Формат ответа

Строго List[str] (json)

## Примеры ответа

["path/to/file_1","path/to/file_2"]

# Структура проекта
"""


def get_project_structure(path=".", prefix="", gpt_format=True, base_path=None, ignore_folders=None,
                          ignore_file_list=None):
    if base_path is None:
        base_path = os.path.abspath(path)
    if ignore_folders is None:
        ignore_folders = []
    if ignore_file_list is None:
        ignore_file_list = []

    if not os.path.exists(path):
        return ""

    entries = sorted(os.listdir(path))
    entries = [e for e in entries if not e.startswith(".")]
    result = ""

    for i, entry in enumerate(entries):
        full_path = os.path.join(path, entry)

        # Проверка на игнорируемые папки
        if os.path.isdir(full_path) and entry in ignore_folders:
            continue

        # Проверка на игнорируемые файлы (по имени)
        if os.path.isfile(full_path) and entry in ignore_file_list:
            continue

        if gpt_format:
            if os.path.isfile(full_path):
                relative_path = os.path.relpath(full_path, start=base_path)
                result += relative_path.replace("/", "\\") + "\n"
        else:
            connector = "└── " if i == len(entries) - 1 else "├── "
            result += prefix + connector + entry + "\n"

        if os.path.isdir(full_path):
            extension = "" if gpt_format else ("    " if i == len(entries) - 1 else "│   ")
            result += get_project_structure(
                full_path,
                prefix + extension,
                gpt_format=gpt_format,
                base_path=base_path,
                ignore_folders=ignore_folders,
                ignore_file_list=ignore_file_list
            )

    return result


def get_project_files_from_list(file_list: list, base_path="."):
    base_path = os.path.abspath(base_path)
    output = ""

    for relative_path in file_list:
        full_path = os.path.join(base_path, relative_path)
        if not os.path.isfile(full_path):
            print(f"{relative_path}\n⚠️ Файл не найден.\n\n")
            continue

        output += f"## {relative_path}\n"
        try:
            with open(full_path, encoding="utf-8") as f:
                output += f.read().strip() + "\n\n"
        except UnicodeDecodeError:
            print("⚠️ Не удалось прочитать файл (ошибка декодирования).\n\n")
        except Exception as e:
            print(f"⚠️ Ошибка при чтении файла: {e}\n\n")

    return output.strip()


def get_gpt_prompt(
        network_tools: NetworkToolsAPI,
        path=None,
        ignore_folders=None,
        task="Выведи все файлы из проекта",
        model="gemini-3-0-flash",
        print_file_list=False,
        file_list=None,
        ignore_file_list=None
):
    """
    Формирует полный промпт для LLM, включая структуру проекта и содержимое необходимых файлов.

    :param network_tools: Объект для работы с API нейросети.
    :param path: Путь к корневой директории проекта. Если None, берется директория вызывающего скрипта.
    :param ignore_folders: Список имен папок, которые нужно полностью исключить из сканирования (например, ['venv', 'node_modules']).
    :param task: Описание задачи, которую должна выполнить нейросеть.
    :param model: Название модели нейросети для предварительного выбора файлов.
    :param print_file_list: Если True, выводит в консоль список файлов, отобранных нейросетью.
    :param file_list: Готовый список путей к файлам. Если передан, нейросеть не будет опрашиваться для поиска файлов.
    :param ignore_file_list: Список конкретных имен файлов (без путей), которые нужно игнорировать (например, ['settings.json']).
    :return: Отформатированная строка промпта со структурой, контентом файлов и текстом задачи.
    """

    # Если путь не указан, берем папку того файла, который вызвал эту функцию
    if path is None:
        caller_frame = inspect.stack()[1]
        caller_filename = caller_frame.filename
        path = os.path.dirname(os.path.abspath(caller_filename))

    root_folder_name = os.path.basename(os.path.abspath(path))
    project_structure = get_project_structure(path, ignore_folders=ignore_folders, ignore_file_list=ignore_file_list)

    if not file_list:
        full_prompt = SEARCH_FILES_PROMPT.format(task) + project_structure

        response = network_tools.chatgpt_api(
            prompt=full_prompt,
            model=model
        )
        response_text = response.response.text.replace("\\\\", "/").replace("//", "/").replace("\\", "/")

        converted, file_list = convert_answer_to_json(response_text, keys=[], start_symbol="[", end_symbol="]")

        # Если не удалось получить список, инициализируем пустым
        if not converted:
            file_list = []

        if print_file_list:
            print("file_list = ", file_list)

    project_files = get_project_files_from_list(file_list, path)

    result = (
        f"# Структура проекта {root_folder_name}\n{project_structure}\n\n"
        f"# Файлы\n{project_files}\n\n"
        f"# Запрос\n\n"
    )

    return result


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
