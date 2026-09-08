import os
import tempfile
from pathlib import Path

from gpt_project_prompter_core.domain import PromptGenerationResult


class OutputDispatcher:
    def dispatch(
        self,
        content: str,
        output_dir: Path,
        max_console_len: int = 2000,
    ) -> PromptGenerationResult:
        char_count = len(content)
        resolved_dir = output_dir.resolve()
        output_file = resolved_dir / "prompt_for_project.txt"

        if char_count > max_console_len:
            resolved_dir.mkdir(parents=True, exist_ok=True)
            temp_file_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=resolved_dir,
                    delete=False,
                ) as temp_file:
                    temp_file.write(content)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                    temp_file_path = Path(temp_file.name)

                os.replace(temp_file_path, output_file)
            except Exception:
                if temp_file_path and temp_file_path.exists():
                    try:
                        temp_file_path.unlink()
                    except OSError:
                        pass
                raise

            print(
                f"\n[PromptEngine] ⚠️ Размер промпта ({char_count} симв.) превышает лимит консоли PyCharm (2000).\n"
                f"[PromptEngine] Промпт успешно сохранен в файл: {output_file}\n"
            )

            return PromptGenerationResult(
                prompt_text=content,
                is_cached=False,
                written_to_file=True,
                output_file_path=output_file,
                character_count=char_count,
            )

        return PromptGenerationResult(
            prompt_text=content,
            is_cached=False,
            written_to_file=False,
            output_file_path=None,
            character_count=char_count,
        )