# Gpt Project prompter

## Возможности

- Автоматическая формулировка задач для LLM

## Установка
```bash
pip install git+https://github.com/Badim41/gpt_project_prompter.git
```
## Использование

```python
from network_tools import NetworkToolsAPI
from gpt_project_prompter import get_gpt_prompt

network = NetworkToolsAPI("your-api-key")

task = "Перевести все запросы к ChatGPT на русский"
path = "C:/Users/you/your_project"
ignore = ["node_modules", "__pycache__"]

prompt = get_gpt_prompt(network, path, ignore, task)
print(prompt)
```

## Пример вывода
```
# Структура проекта
README.md
mineflayer-pathfinder\LICENSE
src/models/gpt.js
...

# Файлы
## src/models/gpt.js
import OpenAIApi from 'openai';
import { getKey, hasKey } from '../utils/keys.js';
import { strictFormat } from '../utils/text.js';
...

# Запрос

```

# Как использовать replacer

Для использования в связке с `get_structure.py`

```commandline
pip install git+https://github.com/Badim41/network_tools.git
pip install git+https://github.com/Badim41/convert_gpt_answer.git
pip install git+https://github.com/Badim41/gpt_project_prompter.git
```

Файлы будут выглядеть так:

**replacer.py:**
```python
from convert_gpt_answer.replacer import run_replacer
# update: pip install git+https://github.com/Badim41/convert_gpt_answer.git
ignore_folders = ["datasets", "images", ".venv", "embed_laboratory", "google_colab_notebooks", ".vscode", "node_modules", "dist", "raw", "legacy_scripts", "legacy_reports"]
ignore_file_list = ["secret.py", "get_structure.py", "replacer.py"]

run_replacer(ignore_folders=ignore_folders, ignore_files=ignore_file_list, check_clipboard=False, skip_clipboard_confirm=True) # convert_gpt_answer v.5.1+
```

**get_structure.py:**
```python
from gpt_project_prompter import get_gpt_prompt
from network_tools import NetworkToolsAPI
import secret

network_api = NetworkToolsAPI(secret.newtork_tools_api_key)
project_path = None # r"C:\...\project_path"
ignore_folders = ["node_modules", "__pycache__", ".agents", "htmlcov", "raw", "tmp", "logs", ".deepsearch", "manifests", "data", "docs", ".venv", "venv"]
ignore_file_list = ["secret.py", "get_structure.py", "replacer.py", ".env"]

task = """
Выведи все файлы проекта для агента (чтобы он имел полный доступ ко всему нужному)

""" # можете уточнить какие файлы выводить

file_list = None

prompt_text = get_gpt_prompt(network_api, project_path, ignore_folders, task, print_file_list=True, file_list=file_list, ignore_file_list=ignore_file_list)
print(prompt_text)

print("""
# ИНСТРУКЦИЯ ПО ВЫВОДУ ПРАВОК И СОЗДАНИЮ ФАЙЛОВ

Все изменения в коде оформляй строго с помощью специальных блоков. Никакого лишнего текста внутри блоков.

---

### 1. Создание НОВЫХ файлов (CREATE)
Если требуется создать новый файл, используй директиву `CREATE`. Все необходимые родительские папки по пути будут созданы автоматически.

Формат:
<<<<<<< CREATE: path/to/new_file.py
import os
import sys

def main():
    print("Содержимое нового файла")
>>>>>>>

Правила:
- В заголовке обязательно указывай путь: `<<<<<<< CREATE: relative/path/to/file.ext`.
- Блок завершается `>>>>>>>`.
- Внутри блока пиши полный исходный код создаваемого файла.

---

### 2. Изменение СУЩЕСТВУЮЩИХ файлов (Search-and-Replace)
Для правки существующего файла используй блоки поиска и замены. Один блок — одна замена.

#### Вариант А: Путь к файлу известен на 100%
Если ты точно знаешь относительный путь или имя редактируемого файла — укажи его сразу после открывающих стрелок в той же строке:
<<<<<<< src/services/auth.py
def old_function():
    print("Старый текст")
=======
def new_function():
    print("Первая строка замены")
    print("Вторая строка замены")
>>>>>>>

#### Вариант Б: Путь к файлу НЕ известен на 100%
Если точный путь или имя файла неизвестны — **НЕ придумывай и не угадывай путь**. Оставляй только стрелки. Replacer автоматически найдет нужный файл в проекте по уникальному коду поиска:
<<<<<<<
def old_function():
    print("Старый текст")
=======
def new_function():
    print("Первая строка замены")
    print("Вторая строка замены")
>>>>>>>

---

### Критически важные правила для блоков замены:
1. **Точность совпадения**: Фрагмент между `<<<<<<<` и `=======` должен с точностью до символа, пробела и отступа соответствовать актуальному коду в файле.
2. **Достаточный контекст**: Особенно если файл не указан (Вариант Б), захватывай 2–3 уникальные строки контекста до и после изменяемого участка (название функции, сигнатура класса, специфичный вызов), чтобы блок не перепутался с похожими местами в других файлах.
3. **Экранирование маркеров**: Если в самом коде нужно использовать символы `<<<<`, `====` или `>>>>`, разбивай их или экранируй (например, `"<" * 4`), чтобы не сломать парсер.
"""
)
```

**secret.py:**
```python
# Получить ключ и 100р в подарок: https://t.me/GPT4_Unlimit_bot?start=api
newtork_tools_api_key = "ВАШ_КЛЮЧ_ЗДЕСЬ"
```

**.gitignore:**
```
# Укажите во избежания утечек секретов:
# search-replace utils

secret.py
replacer.py
get_structure.py
.get_gpt_project
.deepsearch
.deepsearch.db
prompt_for_project.txt
```

После этого:

1. Перейдите в корень вашего проекта, где нужно применить изменения.
2. Запустите модуль:
   ```bash
   python replacer.py
   ```
3. Скопируйте весь текст ответа нейросети (включая команды powershell и блоки `<<<< ==== >>>>`) и вставьте в консоль.
4. На новой пустой строке введите `.,,,` и нажмите **Enter**.
5. Утилита распознает скрипты, предложит их выполнить (y/n), а затем найдет нужные файлы и применит замену кода.
