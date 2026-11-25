# Gpt Project prompter

## Возможности

- Индексация файлов проекта (вес, строки, размеры изображений, длительность аудио/видео) и контуры символов (Python AST, JS/TS, JSON-схемы, SQL и SQLite).
- Автоматическое сворачивание мусорных и слишком больших папок (`pyvenv.cfg`, `node_modules`, тысячи картинок) без их обхода; поддержка `git ls-files`.
- Кэширование выбора LLM и индекса файлов в SQLite (`.get_gpt_project/cache.db`).
- Поддержка масок `dir/*`, `dir/**` в ответе модели, allowlist-безопасность и строгий бюджет символов (`max_structure_chars`, `max_prompt_chars`).
- Модель по умолчанию: `gemini-4-hydrogen`.

## Установка
```bash
pip install git+https://github.com/Badim41/gpt_project_prompter.git
```
## Использование

```python
from network_tools import NetworkToolsAPI
from gpt_project_prompter import DEFAULT_MODEL, get_gpt_prompt, get_project_structure

network = NetworkToolsAPI("your-api-key")

task = "Перевести все запросы к ChatGPT на русский"
path = "C:/Users/you/your_project"
ignore = ["node_modules", "__pycache__"]

# Полный промпт (модель по умолчанию: gemini-4-hydrogen)
prompt = get_gpt_prompt(
    network,
    path,
    ignore,
    task,
    model=DEFAULT_MODEL,
    detail_level=3,                 # 0..3: от одних путей до полных сигнатур
    max_files_in_folder=60,         # x4 автоматически для папок с исходным кодом (>=70%)
    max_files_in_subtree=5000,
    max_structure_chars=60_000,
    max_prompt_chars=500_000,
    use_git=True,
    expand_folders=(),              # принудительно развернуть отдельные папки ("a/b")
    collapse_folders=(),            # принудительно свернуть отдельные папки ("a/b")
    include_edit_instructions=False # добавить инструкцию replacer в конец промпта
)
print(prompt)

# Только структура проекта (detail_level=0 — старый плоский список путей)
structure = get_project_structure(path, ignore_folders=ignore, detail_level=3)
```

## Пример вывода
```
# Структура проекта my_project
README.md  1.2 KB, 40 стр.
assets/  [свернуто: много файлов в папке; 1000 файлов, .png 950, .jpg 50]
src/models/gpt.js  4 KB, 120 стр.
  class GptClient
    async ask(prompt)

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
# ИНСТРУКЦИЯ ПО ВЫВОДУ ПРАВОК, СОЗДАНИЮ ФАЙЛОВ И ЗАПУСКУ СКРИПТОВ

Все изменения в коде оформляй строго с помощью специальных блоков. Никакого лишнего текста внутри блоков.

Порядок в ответе: сначала пояснения обычным текстом (если нужны), затем ОДИН блок ```markdown со всеми правками, затем, если нужно что-то выполнить, блок ```powershell. Блок powershell всегда пиши ОТДЕЛЬНО, вне блока markdown.

Внутри блока markdown должны быть только блоки правок и строка SET_POWERSHELL_TIMEOUT (см. раздел 3). Если внутри блоков правок встречаются строки с тремя бэктиками, открывай и закрывай внешний блок markdown четырьмя бэктиками.

---

### 1. Создание НОВЫХ файлов (CREATE)
Если требуется создать новый файл, используй директиву `CREATE`. Все необходимые родительские папки по пути будут созданы автоматически.

Формат:
\`\`\`markdown
<<<<<<< CREATE: path/to/new_file.py
import os
import sys

def main():
    print("Содержимое нового файла")
>>>>>>>
\`\`\`

Правила:
- В заголовке обязательно указывай путь: `<<<<<<< CREATE: relative/path/to/file.ext`.
- Блок завершается `>>>>>>>`.
- Внутри блока пиши полный исходный код создаваемого файла.
- CREATE не перезаписывает файлы. Если файл уже существует, меняй его блоком поиска и замены (раздел 2), иначе будут отклонены ВСЕ правки ответа.

---

### 2. Изменение СУЩЕСТВУЮЩИХ файлов (Search-and-Replace)
Для правки существующего файла используй блоки поиска и замены. Один блок - одна замена.

#### Вариант А: Путь к файлу известен на 100%
Если ты точно знаешь относительный путь к редактируемому файлу, укажи его сразу после открывающих стрелок в той же строке. Если по этому пути файла нет, блок завершится ошибкой: в других местах проекта файл искать не будут.
\`\`\`markdown
<<<<<<< src/services/auth.py
def old_function():
    print("Старый текст")
=======
def new_function():
    print("Первая строка замены")
    print("Вторая строка замены")
>>>>>>>
\`\`\`

#### Вариант Б: Путь к файлу НЕ известен на 100%
Если точный путь или имя файла неизвестны, **НЕ придумывай и не угадывай путь**. Оставляй только стрелки. Replacer найдёт нужный файл в проекте по коду поиска, но только если этот фрагмент встречается в проекте ровно один раз:
\`\`\`markdown
<<<<<<<
def old_function():
    print("Старый текст")
=======
def new_function():
    print("Первая строка замены")
    print("Вторая строка замены")
>>>>>>>
\`\`\`

Дополнительно:
- Чтобы удалить фрагмент, оставь часть замены пустой (между `=======` и `>>>>>>>` нет строк).
- Несколько блоков для одного файла применяются сверху вниз. Каждый следующий блок ищет свой фрагмент в тексте, уже изменённом предыдущими блоками.

---

### 3. Запуск PowerShell после правок
Если после правок нужно что-то выполнить (сборка, тесты, установка зависимостей), напиши скрипт в отдельном блоке powershell ПОСЛЕ блока markdown:

\`\`\`powershell
dotnet build
\`\`\`

Правила:
- Скрипт запускается ТОЛЬКО если все правки применились. Если хотя бы одна правка не прошла, не применяется ничего и скрипт не запускается.
- Скрипт выполняется в корне проекта и неинтерактивно: не используй Read-Host, pause и всё, что ждёт ввода.
- Несколько блоков powershell склеиваются в один скрипт и выполняются подряд.
- В ответ тебе вернутся вывод скрипта (stdout и stderr вместе) и код завершения.
- Создавать и менять файлы через PowerShell (Out-File, Set-Content, here-string) нельзя. Для этого есть блоки CREATE и поиска и замены.
- Не удаляй и не перемещай файлы, которые не нужны для задачи.

#### Таймаут
Максимальное время работы скрипта задаётся отдельной строкой внутри блока markdown, между блоками правок (не внутри них):

\`\`\`markdown
SET_POWERSHELL_TIMEOUT=600
\`\`\`

- Значение - целое число секунд больше нуля.
- Оцени, сколько скрипт работает, и умножь минимум на 1.5.
- Строка действует только на этот ответ. В каждом следующем ответе со скриптом пиши её заново, иначе возьмётся значение по умолчанию.
- Если скрипт не завершился за это время, он будет принудительно остановлен.
- Слишком большое значение инструмент может уменьшить до своего максимума.

---

### 4. Документация для пользователя
Если пишешь пользователю инструкции с командами для самостоятельного запуска, оформляй их блоком cmd, а не powershell:

\`\`\`cmd
git status
\`\`\`

Блоки с языками powershell и ps1 инструмент выполняет автоматически, поэтому в документации их не используй. Блоки с другими языками не выполняются.

---

### Критически важные правила
1. Точность совпадения. Фрагмент между `<<<<<<<` и `=======` должен с точностью до символа, пробела и отступа соответствовать актуальному коду в файле. Не добавляй пустые строки в начало и конец фрагмента поиска.
2. Единственность. Фрагмент поиска должен встретиться ровно один раз: в указанном файле (вариант А) или во всём проекте (вариант Б). Иначе блок будет отклонён как неоднозначный.
3. Достаточный контекст. Особенно если файл не указан (вариант Б), захватывай 2-3 уникальные строки контекста до и после изменяемого участка (название функции, сигнатура класса, специфичный вызов), чтобы блок не перепутался с похожими местами.
4. Маркеры. Строки `<<<<<<<`, `=======` и `>>>>>>>` пиши ровно по семь символов, с самого начала строки, без отступов. Внутри блоков не должно быть строк, которые начинаются с семи подряд `<`, `=` или `>`. Если такие строки нужны в коде, разбей их на части (например, `"<" * 7`).
5. Пути. Только относительные от корня проекта, с разделителем `/`. Без `..`, без букв дисков (`C:`) и без двоеточий.
6. Всё или ничего. Правки одного ответа применяются как одна транзакция: если хотя бы один блок не сработал, не применится ни один, а скрипт PowerShell не запустится. Проверяй каждый блок.
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
