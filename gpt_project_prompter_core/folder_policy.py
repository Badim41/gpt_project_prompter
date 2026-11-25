from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from gpt_project_prompter_core.constants import (
    DEPENDENCY_DIR_NAMES,
    SOURCE_CODE_LIMIT_MULTIPLIER,
    SOURCE_CODE_RATIO_THRESHOLD,
    SOURCE_EXTENSIONS,
    VENV_DIR_MARKERS,
    VENV_FILE_MARKERS,
)
from gpt_project_prompter_core.domain import CollapsedFolder, CollapseReason


def file_extension_label(filename: str) -> str:
    """Возвращает расширение файла в нижнем регистре или пометку отсутствия расширения."""
    suffix = Path(filename).suffix.lower()
    return suffix if suffix else "(без расш.)"


def top_extensions_from_names(
    names: Sequence[str],
    limit: int = 3,
) -> tuple[tuple[str, int], ...]:
    """Возвращает до limit самых частых расширений с детерминированной сортировкой."""
    counts = Counter(file_extension_label(name) for name in names)
    return top_extensions_from_counts(counts, limit=limit)


def top_extensions_from_counts(
    counts: Counter[str],
    limit: int = 3,
) -> tuple[tuple[str, int], ...]:
    """Сортирует счётчик расширений по убыванию частоты и возрастанию имени."""
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return tuple(ordered[:limit])


def format_collapsed_folder(folder: CollapsedFolder) -> str:
    """Форматирует строку свёрнутой папки для плоской структуры и дерева."""
    if folder.file_count is None:
        return f"{folder.rel_path}/  [свернуто: {folder.reason.value}]"
    prefix = "≥" if folder.is_lower_bound else ""
    count_part = f"{prefix}{folder.file_count} файлов"
    if folder.top_extensions:
        ext_part = ", ".join(f"{ext} {cnt}" for ext, cnt in folder.top_extensions)
        return f"{folder.rel_path}/  [свернуто: {folder.reason.value}; {count_part}, {ext_part}]"
    return f"{folder.rel_path}/  [свернуто: {folder.reason.value}; {count_part}]"


class FolderPolicy:
    """Единая политика сворачивания папок для WalkFileSource и GitFileSource."""

    def __init__(
        self,
        max_files_in_folder: int,
        max_files_in_subtree: int,
        expand_folders: frozenset[str] = frozenset(),
        collapse_folders: frozenset[str] = frozenset(),
    ) -> None:
        self._max_files_in_folder = max_files_in_folder
        self._max_files_in_subtree = max_files_in_subtree
        self._expand_folders = expand_folders
        self._collapse_folders = collapse_folders

    @property
    def max_files_in_subtree(self) -> int:
        """Возвращает лимит числа файлов в одном поддереве."""
        return self._max_files_in_subtree

    @property
    def expand_folders(self) -> frozenset[str]:
        """Возвращает множество принудительно развёрнутых папок."""
        return self._expand_folders

    def is_expanded(self, rel_dir: str) -> bool:
        """Проверяет, отменено ли авто-сворачивание для папки rel_dir."""
        return rel_dir in self._expand_folders

    def is_configured_collapse(self, rel_dir: str) -> bool:
        """Проверяет принудительное сворачивание папки по настройкам."""
        return bool(rel_dir) and rel_dir in self._collapse_folders

    def detect_junk_marker(self, rel_dir: str, abs_dir: Path) -> CollapseReason | None:
        """Проверяет маркеры мусорной папки через O(1) stat-проверки без обхода."""
        if not rel_dir or self.is_expanded(rel_dir):
            return None
        if abs_dir.name in DEPENDENCY_DIR_NAMES:
            return CollapseReason.DEPENDENCIES
        if self._has_venv_marker(abs_dir):
            return CollapseReason.VENV
        if self._has_nested_git_marker(abs_dir):
            return CollapseReason.NESTED_GIT
        return None

    def effective_direct_limit(self, direct_file_names: Sequence[str]) -> int:
        """Вычисляет лимит прямых файлов с множителем 4x для папок с исходным кодом."""
        if not direct_file_names:
            return self._max_files_in_folder
        source_count = sum(
            1 for name in direct_file_names if Path(name).suffix.lower() in SOURCE_EXTENSIONS
        )
        if source_count / len(direct_file_names) >= SOURCE_CODE_RATIO_THRESHOLD:
            return self._max_files_in_folder * SOURCE_CODE_LIMIT_MULTIPLIER
        return self._max_files_in_folder

    def should_collapse_direct(
        self,
        rel_dir: str,
        direct_file_names: Sequence[str],
    ) -> bool:
        """Определяет, превышает ли число прямых файлов эффективный лимит."""
        if not rel_dir or self.is_expanded(rel_dir):
            return False
        return len(direct_file_names) > self.effective_direct_limit(direct_file_names)

    def should_collapse_subtree(self, rel_dir: str, subtree_count: int) -> bool:
        """Определяет, превышает ли число файлов в поддереве max_files_in_subtree."""
        if not rel_dir or self.is_expanded(rel_dir):
            return False
        return subtree_count > self._max_files_in_subtree

    @staticmethod
    def _has_venv_marker(abs_dir: Path) -> bool:
        for marker_file in VENV_FILE_MARKERS:
            if (abs_dir / marker_file).is_file():
                return True
        for marker_dir in VENV_DIR_MARKERS:
            if (abs_dir / marker_dir).is_dir():
                return True
        return False

    @staticmethod
    def _has_nested_git_marker(abs_dir: Path) -> bool:
        try:
            return (abs_dir / ".git").exists()
        except OSError:
            return False
