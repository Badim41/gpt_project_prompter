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