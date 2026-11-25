from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from gpt_project_prompter_core.constants import (
    DEFAULT_MAX_PROMPT_CHARS,
    DEFAULT_MAX_STRUCTURE_CHARS,
    PROMPT_RESERVE_CHARS,
)
from gpt_project_prompter_core.domain import (
    ProjectStructureSnapshot,
    SelectedFile,
    Selection,
)
from gpt_project_prompter_core.edit_instructions import get_edit_instructions
from gpt_project_prompter_core.extractors.base import FileIndexEntry
from gpt_project_prompter_core.interfaces import SafeFileReaderProtocol
from gpt_project_prompter_core.renderer import format_facts, render_structure

_OVERFLOW_NOTE = "[только контур: файл не поместился в лимит, запроси его целиком]"


@dataclass(frozen=True, slots=True)
class BuiltPrompt:
    """Собранный финальный промпт и список предупреждений бюджета."""

    text: str
    warnings: tuple[str, ...]


def build_final_prompt(
    *,
    root_path: Path,
    snapshot: ProjectStructureSnapshot,
    index: Mapping[str, FileIndexEntry],
    selection: Selection,
    file_reader: SafeFileReaderProtocol,
    max_file_size_bytes: int,
    max_structure_chars: int = DEFAULT_MAX_STRUCTURE_CHARS,
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS,
    expand_folders: frozenset[str] = frozenset(),
    include_edit_instructions: bool = False,
) -> BuiltPrompt:
    """Собирает финальный промпт из структуры, файлов и инструкций в пределах бюджета."""
    ordered = _order_selected_files(selection.files, index)
    root_name = root_path.resolve().name
    truncated_header = _format_truncated_patterns(selection)
    footer = _build_footer(include_edit_instructions)
    header = f"# Структура проекта {root_name}\n"
    mid = "\n\n# Файлы\n"

    file_blocks: list[tuple[str, str, str]] = []
    for sel in ordered:
        read_res = file_reader.read_one(
            root_path=root_path,
            relative_path=sel.path,
            max_file_size_bytes=max_file_size_bytes,
        )
        full_block = f"## {sel.path}\n{read_res.text}\n\n"
        stub_block = _build_overflow_block(sel.path, index.get(sel.path))
        file_blocks.append((sel.path, full_block, stub_block))

    reserve = PROMPT_RESERVE_CHARS if max_prompt_chars > PROMPT_RESERVE_CHARS * 2 else 0
    base_overhead = len(header) + len(mid) + len(truncated_header) + len(footer)

    est_struct_budget = min(max_structure_chars, max(0, max_prompt_chars - base_overhead))
    initial_struct = render_structure(
        snapshot,
        index,
        detail_level=3,
        max_structure_chars=est_struct_budget,
        expand_folders=expand_folders,
    )

    files_budget = max(0, max_prompt_chars - base_overhead - len(initial_struct.text) - reserve)
    included_flags = _select_fitting_blocks(file_blocks, files_budget)

    included_paths = frozenset(
        path for (path, _, _), is_full in zip(file_blocks, included_flags) if is_full
    )
    rendered_files = _join_file_blocks(file_blocks, included_flags, truncated_header)

    remaining_for_struct = max(0, max_prompt_chars - base_overhead - len(rendered_files))
    final_struct = render_structure(
        snapshot,
        index,
        detail_level=3,
        max_structure_chars=min(max_structure_chars, remaining_for_struct),
        expand_folders=expand_folders,
        exclude_outlines_for=included_paths,
    )

    prompt = f"{header}{final_struct.text}{mid}{rendered_files}{footer}"
    warnings = list(final_struct.warnings)

    # При переполнении ужимаем полные файлы с конца, затем обрезаем контурные заглушки
    if len(prompt) > max_prompt_chars:
        prompt, extra_warnings = _enforce_hard_limit(
            header=header,
            mid=mid,
            footer=footer,
            truncated_header=truncated_header,
            snapshot=snapshot,
            index=index,
            file_blocks=file_blocks,
            included_flags=included_flags,
            max_structure_chars=max_structure_chars,
            max_prompt_chars=max_prompt_chars,
            expand_folders=expand_folders,
        )
        warnings.extend(extra_warnings)

    return BuiltPrompt(text=prompt, warnings=tuple(dict.fromkeys(warnings)))


def _order_selected_files(
    files: tuple[SelectedFile, ...],
    index: Mapping[str, FileIndexEntry],
) -> list[SelectedFile]:
    explicit = [item for item in files if not item.from_pattern]
    from_patterns = sorted(
        (item for item in files if item.from_pattern),
        key=lambda item: (_file_size(item.path, index), item.path),
    )
    return explicit + from_patterns


def _file_size(rel_path: str, index: Mapping[str, FileIndexEntry]) -> int:
    entry = index.get(rel_path)
    return entry.facts.size_bytes if entry is not None else 0


def _format_truncated_patterns(selection: Selection) -> str:
    if not selection.truncated_patterns:
        return ""
    lines = [
        f"[маска {tp.pattern}: показано {tp.shown} из {tp.total}]"
        for tp in selection.truncated_patterns
    ]
    return "\n".join(lines) + "\n\n"


def _build_footer(include_edit_instructions: bool) -> str:
    if include_edit_instructions:
        return f"\n\n# Запрос\n\n{get_edit_instructions()}\n"
    return "\n\n# Запрос\n\n"


def _build_overflow_block(rel_path: str, entry: FileIndexEntry | None) -> str:
    details = _overflow_outline_text(entry)
    return f"## {rel_path}\n{_OVERFLOW_NOTE}\n{details}\n\n"


def _overflow_outline_text(entry: FileIndexEntry | None) -> str:
    if entry is None:
        return "(индекс недоступен)"
    if entry.outline is not None and entry.outline.detailed_lines:
        return "\n".join(entry.outline.detailed_lines)
    return format_facts(entry.facts)


def _select_fitting_blocks(
    file_blocks: list[tuple[str, str, str]],
    files_budget: int,
) -> list[bool]:
    stub_total = sum(len(stub) for _, _, stub in file_blocks)
    used = stub_total
    flags: list[bool] = []
    for _, full_block, stub_block in file_blocks:
        delta = len(full_block) - len(stub_block)
        if used + delta <= files_budget:
            flags.append(True)
            used += delta
        else:
            flags.append(False)
    return flags


def _join_file_blocks(
    file_blocks: list[tuple[str, str, str]],
    included_flags: list[bool],
    truncated_header: str,
) -> str:
    body = "".join(
        full_block if is_full else stub_block
        for (_, full_block, stub_block), is_full in zip(file_blocks, included_flags)
    ).strip()
    if truncated_header and body:
        return f"{truncated_header}{body}"
    return (truncated_header + body).strip()


def _enforce_hard_limit(
    *,
    header: str,
    mid: str,
    footer: str,
    truncated_header: str,
    snapshot: ProjectStructureSnapshot,
    index: Mapping[str, FileIndexEntry],
    file_blocks: list[tuple[str, str, str]],
    included_flags: list[bool],
    max_structure_chars: int,
    max_prompt_chars: int,
    expand_folders: frozenset[str],
) -> tuple[str, list[str]]:
    flags = list(included_flags)
    warnings: list[str] = []

    for idx in range(len(flags) - 1, -1, -1):
        if not flags[idx]:
            continue
        flags[idx] = False
        prompt, st_warns = _render_candidate(
            header, mid, footer, truncated_header, snapshot, index,
            file_blocks, flags, max_structure_chars, max_prompt_chars, expand_folders,
        )
        if len(prompt) <= max_prompt_chars:
            return prompt, list(st_warns)

    prompt, st_warns = _render_candidate(
        header, mid, footer, truncated_header, snapshot, index,
        file_blocks, flags, max_structure_chars, max_prompt_chars, expand_folders,
    )
    warnings.extend(st_warns)
    if len(prompt) <= max_prompt_chars:
        return prompt, warnings

    warnings.append(
        f"Промпт был обрезан до лимита max_prompt_chars ({max_prompt_chars})."
    )
    return prompt[:max_prompt_chars], warnings


def _render_candidate(
    header: str,
    mid: str,
    footer: str,
    truncated_header: str,
    snapshot: ProjectStructureSnapshot,
    index: Mapping[str, FileIndexEntry],
    file_blocks: list[tuple[str, str, str]],
    flags: list[bool],
    max_structure_chars: int,
    max_prompt_chars: int,
    expand_folders: frozenset[str],
) -> tuple[str, tuple[str, ...]]:
    included = frozenset(
        path for (path, _, _), is_full in zip(file_blocks, flags) if is_full
    )
    rendered_files = _join_file_blocks(file_blocks, flags, truncated_header)
    overhead = len(header) + len(mid) + len(footer) + len(rendered_files)
    struct_limit = min(max_structure_chars, max(0, max_prompt_chars - overhead))
    st = render_structure(
        snapshot,
        index,
        detail_level=3,
        max_structure_chars=struct_limit,
        expand_folders=expand_folders,
        exclude_outlines_for=included,
    )
    return f"{header}{st.text}{mid}{rendered_files}{footer}", st.warnings
