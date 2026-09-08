from pathlib import Path
from typing import Sequence

from gpt_project_prompter_core.exceptions import SecurityPathTraversalError


class SafeFileReader:
    def read_files_content(
        self,
        root_path: Path,
        relative_paths: Sequence[str],
        max_file_size_bytes: int = 2_097_152,
    ) -> str:
        resolved_root = root_path.resolve()
        output_parts: list[str] = []

        for rel_str in relative_paths:
            clean_rel = rel_str.strip().replace("\\", "/")
            target = (resolved_root / clean_rel).resolve()

            if not target.is_relative_to(resolved_root):
                raise SecurityPathTraversalError(
                    f"Обнаружена попытка выхода за пределы проекта (Path Traversal): '{rel_str}'"
                )

            if not target.is_file():
                output_parts.append(f"## {clean_rel}\n⚠️ Файл не найден.\n\n")
                continue

            stat_result = target.stat()
            if stat_result.st_size > max_file_size_bytes:
                output_parts.append(
                    f"## {clean_rel}\n⚠️ Файл превышает лимит размера "
                    f"({stat_result.st_size} > {max_file_size_bytes} байт). Пропущен.\n\n"
                )
                continue

            try:
                with target.open("rb") as binary_file:
                    initial_chunk = binary_file.read(1024)
                    if b"\x00" in initial_chunk:
                        output_parts.append(f"## {clean_rel}\n⚠️ Бинарный файл пропущен.\n\n")
                        continue

                    binary_file.seek(0)
                    content = binary_file.read().decode("utf-8")
                    output_parts.append(f"## {clean_rel}\n{content.strip()}\n\n")
            except UnicodeDecodeError:
                output_parts.append(
                    f"## {clean_rel}\n⚠️ Ошибка декодирования UTF-8 (файл не в текстовом формате).\n\n"
                )
            except (OSError, PermissionError) as io_err:
                output_parts.append(f"## {clean_rel}\n⚠️ Ошибка ввода-вывода: {io_err}\n\n")

        return "".join(output_parts).strip()