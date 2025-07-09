import json
import os
import re

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


def quick_fix_json(json_string):
    """
    Быстрое исправление JSON - заменяет внутренние кавычки на одинарные
    """

    # Находим все строковые значения и заменяем в них кавычки

    def fix_value(match):
        value = match.group(1)
        # Заменяем все внутренние кавычки на одинарные
        return ':"' + value.replace('"', "'") + '"'

    # Паттерн для значений в кавычках после двоеточия
    pattern = r':\s*"([^"]*(?:"[^"]*)*)"'

    # Исправляем JSON
    fixed = json_string
    while True:
        new_fixed = re.sub(pattern, fix_value, fixed)
        if new_fixed == fixed:
            break
        fixed = new_fixed

    return fixed


def convert_answer_to_json(answer: str, keys, start_symbol="{", end_symbol="}", attemtp=1) -> [bool, dict]:
    if isinstance(keys, str):
        keys = [keys]

    answer = answer.replace(" ", "").replace(" None", " null").replace(" False", " false").replace(" True", " true")

    if attemtp == 2:
        # 'find'
        if start_symbol in answer and end_symbol in answer:
            answer = answer[answer.find(start_symbol):]
            answer = answer[:answer.find(end_symbol) + 1]
        else:
            return False, "Не json"
    elif attemtp == 1:
        # 'rfind'
        if start_symbol in answer and end_symbol in answer:
            answer = answer[answer.find(start_symbol):]
            answer = answer[:answer.rfind(end_symbol) + 1]
        else:
            return False, "Не json"
    else:
        return False, "ERROR"

    try:
        try:
            response = json.loads(answer)
        except json.JSONDecodeError as e:
            answer = quick_fix_json(answer)
            response = json.loads(answer)

        for key in keys:
            if response.get(key, "NULL_VALUE") == "NULL_VALUE":
                return False, "Нет ключа"
        return True, response
    except json.JSONDecodeError as e:
        return convert_answer_to_json(answer=answer, keys=keys, start_symbol=start_symbol, end_symbol=end_symbol,
                                      attemtp=attemtp + 1)


def get_project_structure(path=".", prefix="", gpt_format=True, base_path=None, ignore_folders=None):
    if base_path is None:
        base_path = os.path.abspath(path)
    if ignore_folders is None:
        ignore_folders = []

    entries = sorted(os.listdir(path))
    entries = [e for e in entries if not e.startswith(".")]  # Игнорируем скрытые
    result = ""

    for i, entry in enumerate(entries):
        full_path = os.path.join(path, entry)

        # Пропускаем игнорируемые папки
        if os.path.isdir(full_path) and entry in ignore_folders:
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
                ignore_folders=ignore_folders
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


def get_gpt_prompt(network_tools, path, ignore_folders, task, model="claude-4-opus-thinking", print_file_list=False,
                   file_list=None):
    project_structure = get_project_structure(path, ignore_folders=ignore_folders)

    if not file_list:
        full_prompt = SEARCH_FILES_PROMPT.format(task) + project_structure

        response = network_tools.chatgpt_api(
            prompt=full_prompt,
            model=model
        )

        converted, file_list = convert_answer_to_json(response.response.text, keys=[], start_symbol="[", end_symbol="]")

        if print_file_list:
            print("file_list", file_list)

    project_files = get_project_files_from_list(file_list, path)

    result = (
        f"# Структура проекта\n{project_structure}\n\n"
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
