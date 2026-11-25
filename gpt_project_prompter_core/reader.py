from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from gpt_project_prompter_core.constants import MAX_TEXT_READ_BYTES
from gpt_project_prompter_core.domain import FileReadResult, FileReadStatus
from gpt_project_prompter_core.exceptions import SecurityPathTraversalError


class SafeFileReader:
    """Безопасно читает текстовые файлы в пределах корня проекта."""

    def read_one(
        self,
        root_path: Path,
        relative_path: str,
        max_file_size_bytes: int = MAX_TEXT_READ_BYTES,
    ) -> FileReadResult:
        """Читает один файл с проверкой Path Traversal, размера и бинарности."""
        resolved_root = root_path.resolve()
        clean_rel = relative_path.strip().replace("\\", "/")
        target = (resolved_root / clean_rel).resolve()

        if not target.is_relative_to(resolved_root):
            raise SecurityPathTraversalError(
                f"Обнаружена попытка выхода за пределы проекта (Path Traversal): '{relative_path}'"
            )

        if not target.is_file():
            return FileReadResult(status=FileReadStatus.NOT_FOUND, text="⚠️ Файл не найден.")

        try:
            stat_result = target.stat()
        except OSError as io_err:
            return FileReadResult(
                status=FileReadStatus.IO_ERROR,
                text=f"⚠️ Ошибка ввода-вывода: {io_err}",
            )

        if stat_result.st_size > max_file_size_bytes:
            return FileReadResult(
                status=FileReadStatus.TOO_LARGE,
                text=(
                    f"⚠️ Файл превышает лимит размера "
                    f"({stat_result.st_size} > {max_file_size_bytes} байт). Пропущен."
                ),
            )

        return _read_text_payload(target)

    def read_files_content(
        self,
        root_path: Path,
        relative_paths: Sequence[str],
        max_file_size_bytes: int = MAX_TEXT_READ_BYTES,
    ) -> str:
        """Читает последовательность файлов в единый Markdown-блок."""
        output_parts: list[str] = []
        for rel_str in relative_paths:
            clean_rel = rel_str.strip().replace("\\", "/")
            res = self.read_one(
                root_path=root_path,
                relative_path=clean_rel,
                max_file_size_bytes=max_file_size_bytes,
            )
            output_parts.append(f"## {clean_rel}\n{res.text}\n\n")
        return "".join(output_parts).strip()


def _read_text_payload(target: Path) -> FileReadResult:
    try:
        with target.open("rb") as binary_file:
            initial_chunk = binary_file.read(1024)
            if b"\x00" in initial_chunk:
                return FileReadResult(
                    status=FileReadStatus.BINARY,
                    text="⚠️ Бинарный файл пропущен.",
                )
            binary_file.seek(0)
            content = binary_file.read().decode("utf-8")
            return FileReadResult(status=FileReadStatus.OK, text=content.strip())
    except UnicodeDecodeError:
        return FileReadResult(
            status=FileReadStatus.DECODE_ERROR,
            text="⚠️ Ошибка декодирования UTF-8 (файл не в текстовом формате).",
        )
    except (OSError, PermissionError) as io_err:
        return FileReadResult(
            status=FileReadStatus.IO_ERROR,
            text=f"⚠️ Ошибка ввода-вывода: {io_err}",
        )
