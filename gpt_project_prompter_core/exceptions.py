class PromptEngineError(Exception):
    """Базовое исключение подсистемы генерации промптов."""


class CacheStorageError(PromptEngineError):
    """Ошибка операций чтения/записи в хранилище кэша SQLite."""


class CorruptedCacheError(PromptEngineError):
    """Нарушение целостности или формата сериализованных данных кэша."""


class SecurityPathTraversalError(PromptEngineError):
    """Попытка чтения файла вне доверенного корня проекта (Path Traversal)."""


class ProjectScanIOError(PromptEngineError):
    """Ошибка ввода-вывода при сканировании файловой структуры проекта."""


class FileReadLimitExceededError(PromptEngineError):
    """Превышение максимально допустимого размера файла при чтении."""