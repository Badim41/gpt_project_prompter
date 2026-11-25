from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from gpt_project_prompter_core.domain import (
    CollapsedFolder,
    CollapseReason,
    ProjectStructureSnapshot,
    RenderedStructure,
)
from gpt_project_prompter_core.extractors.base import FileFacts, FileIndexEntry, FileKind
from gpt_project_prompter_core.folder_policy import (
    file_extension_label,
    format_collapsed_folder,
    top_extensions_from_counts,
)
from gpt_project_prompter_core.media_probe import format_duration, format_size


def format_facts(facts: FileFacts) -> str:
    """Форматирует факты файла через запятую для уровня 1 и выше."""
    parts: list[str] = [format_size(facts.size_bytes)]
    if facts.kind is FileKind.TEXT and facts.line_count is not None:
        parts.append(f"{facts.line_count} стр.")
    elif facts.kind is FileKind.IMAGE:
        if facts.width is not None and facts.height is not None:
            parts.append(f"{facts.width}x{facts.height}")
    elif facts.kind is FileKind.AUDIO:
        if facts.duration_seconds is not None:
            parts.append(format_duration(facts.duration_seconds))
    elif facts.kind is FileKind.VIDEO:
        if facts.width is not None and facts.height is not None:
            parts.append(f"{facts.width}x{facts.height}")
        if facts.duration_seconds is not None:
            parts.append(format_duration(facts.duration_seconds))
    return ", ".join(parts)


def render_file_entry(
    rel_path: str,
    entry: FileIndexEntry | None,
    level: int,
    *,
    suppress_outline: bool = False,
) -> str:
    """Рендерит один файл на заданном уровне детализации (0..3)."""
    eff_level = min(level, 1) if suppress_outline else level
    if eff_level <= 0 or entry is None:
        return rel_path
    header = f"{rel_path}  {format_facts(entry.facts)}"
    if eff_level == 1 or entry.outline is None:
        return header
    outline_lines = (
        entry.outline.compact_lines if eff_level == 2 else entry.outline.detailed_lines
    )
    if not outline_lines:
        return header
    indented = "\n".join(f"  {line}" for line in outline_lines)
    return f"{header}\n{indented}"


def render_tree_from_snapshot(
    snapshot: ProjectStructureSnapshot,
    prefix: str = "",
) -> str:
    """Строит ASCII-дерево из уже собранного снимка без повторного обхода диска."""
    tree: dict[str, object] = {}
    for rel_path in snapshot.canonical_relative_paths:
        _insert_tree_leaf(tree, rel_path.split("/"), None)
    for collapsed in snapshot.collapsed_folders:
        _insert_tree_leaf(tree, collapsed.rel_path.split("/"), collapsed)
    return _format_tree_node(tree, prefix)


def _insert_tree_leaf(
    node: dict[str, object],
    parts: Sequence[str],
    collapsed: CollapsedFolder | None,
) -> None:
    cur = node
    for seg in parts[:-1]:
        nxt = cur.get(seg)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[seg] = nxt
        cur = nxt
    leaf = parts[-1]
    cur[leaf] = collapsed


def _format_tree_node(node: dict[str, object], prefix: str) -> str:
    items = sorted(node.items(), key=lambda pair: pair[0])
    total = len(items)
    parts: list[str] = []
    for idx, (name, val) in enumerate(items):
        is_last = idx == total - 1
        connector = "└── " if is_last else "├── "
        if isinstance(val, CollapsedFolder):
            label = format_collapsed_folder(val).split("/")[-1]
            parts.append(f"{prefix}{connector}{name}/{label}\n")
        elif isinstance(val, dict):
            parts.append(f"{prefix}{connector}{name}\n")
            ext = "    " if is_last else "│   "
            parts.append(_format_tree_node(val, prefix + ext))
        else:
            parts.append(f"{prefix}{connector}{name}\n")
    return "".join(parts)


@dataclass
class _RenderState:
    files: list[str]
    collapsed: list[CollapsedFolder]
    group_levels: dict[str, int]


def render_structure(
    snapshot: ProjectStructureSnapshot,
    index: Mapping[str, FileIndexEntry],
    *,
    detail_level: int = 3,
    max_structure_chars: int = 60_000,
    expand_folders: frozenset[str] = frozenset(),
    exclude_outlines_for: frozenset[str] = frozenset(),
) -> RenderedStructure:
    """Рендерит плоскую структуру с детерминированной деградацией по бюджету."""
    groups = {_top_group(p) for p in snapshot.canonical_relative_paths}
    state = _RenderState(
        files=list(snapshot.canonical_relative_paths),
        collapsed=list(snapshot.collapsed_folders),
        group_levels={g: detail_level for g in sorted(groups)},
    )

    text = _assemble_text(state, index, exclude_outlines_for)
    if len(text) <= max_structure_chars:
        return RenderedStructure(text=text)

    # Шаг 2: снижаем уровень детализации самой тяжёлой папки верхнего уровня
    while len(text) > max_structure_chars:
        reducible = [g for g, lvl in state.group_levels.items() if lvl > 0]
        if not reducible:
            break
        heaviest = _pick_heaviest_group(state, reducible, index, exclude_outlines_for)
        state.group_levels[heaviest] -= 1
        text = _assemble_text(state, index, exclude_outlines_for)

    # Шаг 3: сворачиваем самые большие папки, кроме защищённых expand_folders
    while len(text) > max_structure_chars:
        folder = _pick_collapsible_folder(state, expand_folders)
        if folder is None:
            break
        _collapse_folder_in_state(state, folder)
        text = _assemble_text(state, index, exclude_outlines_for)

    warnings: tuple[str, ...] = ()
    if len(text) > max_structure_chars:
        warnings = (
            f"Структура проекта ({len(text)} симв.) не поместилась в лимит "
            f"max_structure_chars ({max_structure_chars}).",
        )
    return RenderedStructure(text=text, warnings=warnings)


def _top_group(rel_path: str) -> str:
    return rel_path.split("/", 1)[0] if "/" in rel_path else ""


def _assemble_text(
    state: _RenderState,
    index: Mapping[str, FileIndexEntry],
    exclude_outlines_for: frozenset[str],
) -> str:
    rows: list[tuple[str, str]] = []
    for rel_path in state.files:
        lvl = state.group_levels.get(_top_group(rel_path), 0)
        rendered = render_file_entry(
            rel_path,
            index.get(rel_path),
            lvl,
            suppress_outline=rel_path in exclude_outlines_for,
        )
        rows.append((rel_path, rendered))
    for folder in state.collapsed:
        rows.append((f"{folder.rel_path}/", format_collapsed_folder(folder)))
    rows.sort(key=lambda pair: pair[0])
    if not rows:
        return ""
    return "\n".join(text for _, text in rows) + "\n"


def _pick_heaviest_group(
    state: _RenderState,
    candidates: Sequence[str],
    index: Mapping[str, FileIndexEntry],
    exclude_outlines_for: frozenset[str],
) -> str:
    sizes: dict[str, int] = {g: 0 for g in candidates}
    for rel_path in state.files:
        grp = _top_group(rel_path)
        if grp not in sizes:
            continue
        lvl = state.group_levels[grp]
        rendered = render_file_entry(
            rel_path,
            index.get(rel_path),
            lvl,
            suppress_outline=rel_path in exclude_outlines_for,
        )
        sizes[grp] += len(rendered) + 1
    return min(candidates, key=lambda g: (-sizes[g], g))


def _is_protected(folder: str, expand_folders: frozenset[str]) -> bool:
    if folder in expand_folders:
        return True
    prefix = f"{folder}/"
    return any(exp.startswith(prefix) for exp in expand_folders)


def _pick_collapsible_folder(
    state: _RenderState,
    expand_folders: frozenset[str],
) -> str | None:
    folder_weights: dict[str, int] = {}
    for rel_path in state.files:
        if "/" not in rel_path:
            continue
        target = _shallowest_collapsible_prefix(rel_path, expand_folders)
        if target is not None:
            folder_weights[target] = folder_weights.get(target, 0) + len(rel_path) + 1
    if not folder_weights:
        return None
    return min(folder_weights.keys(), key=lambda f: (-folder_weights[f], f))


def _shallowest_collapsible_prefix(
    rel_path: str,
    expand_folders: frozenset[str],
) -> str | None:
    parts = rel_path.split("/")[:-1]
    cur = ""
    for seg in parts:
        cur = f"{cur}/{seg}" if cur else seg
        if not _is_protected(cur, expand_folders):
            return cur
    return None


def _collapse_folder_in_state(state: _RenderState, folder: str) -> None:
    prefix = f"{folder}/"
    remaining_files: list[str] = []
    ext_counts: Counter[str] = Counter()
    removed_count = 0

    for rel_path in state.files:
        if rel_path.startswith(prefix):
            removed_count += 1
            ext_counts[file_extension_label(rel_path)] += 1
        else:
            remaining_files.append(rel_path)

    remaining_collapsed = [
        cf for cf in state.collapsed if cf.rel_path != folder and not cf.rel_path.startswith(prefix)
    ]
    remaining_collapsed.append(
        CollapsedFolder(
            rel_path=folder,
            reason=CollapseReason.BUDGET_LIMIT,
            file_count=removed_count,
            is_lower_bound=False,
            top_extensions=top_extensions_from_counts(ext_counts),
        )
    )
    state.files = remaining_files
    state.collapsed = remaining_collapsed
