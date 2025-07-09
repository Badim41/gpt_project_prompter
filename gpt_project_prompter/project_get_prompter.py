import os

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


def get_gpt_prompt(network_tools, path, ignore_folders, task, model="claude-4-opus-thinking"):
    from discord_tools.str_tools import convert_answer_to_json

    project_structure = get_project_structure(path, ignore_folders=ignore_folders)
    full_prompt = SEARCH_FILES_PROMPT.format(task) + project_structure

    response = network_tools.chatgpt_api(
        prompt=full_prompt,
        model=model
    )

    converted, file_list = convert_answer_to_json(response.response.text, keys=[], start_symbol="[", end_symbol="]")

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
