from __future__ import annotations

from collections.abc import Sequence
import re

from gpt_project_prompter_core.constants import MAX_FILES_PER_PATTERN
from gpt_project_prompter_core.domain import (
    SelectedFile,
    Selection,
    TruncatedPattern,
)

_GLOB_CHARS: frozenset[str] = frozenset("*?[")


def has_glob_chars(pattern: str) -> bool:
    """Проверяет наличие метасимволов маски *, ? или [."""
    return any(ch in _GLOB_CHARS for ch in pattern)


def normalize_candidate(raw: str) -> str | None:
    """Нормализует путь или маску и отклоняет небезопасные варианты."""
    cleaned = raw.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if not cleaned or cleaned.startswith("/") or ".." in cleaned or ":" in cleaned:
        return None
    return cleaned.rstrip("/")


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Переводит файловую маску (*, ?, [...], **/, /**) в регулярное выражение."""
    parts: list[str] = [r"\A"]
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
        elif pattern[i:] == "/**":
            parts.append("/.*")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            cls_expr, next_i = _parse_char_class(pattern, i)
            parts.append(cls_expr)
            i = next_i
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    parts.append(r"\Z")
    return re.compile("".join(parts))


def _parse_char_class(pattern: str, start: int) -> tuple[str, int]:
    j = start + 1
    n = len(pattern)
    if j < n and pattern[j] in ("!", "^"):
        j += 1
    if j < n and pattern[j] == "]":
        j += 1
    while j < n and pattern[j] != "]" and pattern[j] != "/":
        j += 1
    if j >= n or pattern[j] != "]":
        return re.escape("["), start + 1
    inner = pattern[start + 1 : j]
    if inner.startswith("!"):
        inner = "^" + inner[1:]
    inner = inner.replace("\\", r"\\")
    return f"[{inner}]", j + 1


def resolve_selection(
    raw_items: Sequence[str],
    allowlist: Sequence[str],
    *,
    is_explicit_user_list: bool = False,
) -> Selection:
    """Раскрывает пути и маски по allowlist с дедупликацией и лимитом на маску."""
    sorted_allowlist = sorted(allowlist)
    allow_set = frozenset(sorted_allowlist)
    selected: list[SelectedFile] = []
    seen: set[str] = set()
    skipped: list[str] = []
    truncated: list[TruncatedPattern] = []

    for raw in raw_items:
        if is_explicit_user_list and not has_glob_chars(raw):
            clean = raw.strip().replace("\\", "/")
            if clean and clean not in seen:
                seen.add(clean)
                selected.append(SelectedFile(path=clean, from_pattern=False))
            continue

        norm = normalize_candidate(raw)
        if norm is None:
            skipped.append(raw)
            continue

        if has_glob_chars(norm):
            _apply_pattern(norm, sorted_allowlist, selected, seen, skipped, truncated)
            continue

        if norm in allow_set:
            if norm not in seen:
                seen.add(norm)
                selected.append(SelectedFile(path=norm, from_pattern=False))
            continue

        folder_prefix = f"{norm}/"
        if any(p.startswith(folder_prefix) for p in sorted_allowlist):
            _apply_pattern(
                f"{norm}/*", sorted_allowlist, selected, seen, skipped, truncated, raw_fallback=raw
            )
            continue

        skipped.append(raw)

    return Selection(
        files=tuple(selected),
        skipped=tuple(skipped),
        truncated_patterns=tuple(truncated),
    )


def _apply_pattern(
    pattern: str,
    sorted_allowlist: Sequence[str],
    selected: list[SelectedFile],
    seen: set[str],
    skipped: list[str],
    truncated: list[TruncatedPattern],
    *,
    raw_fallback: str | None = None,
) -> None:
    regex = glob_to_regex(pattern)
    matches = [path for path in sorted_allowlist if regex.match(path) is not None]
    if not matches:
        skipped.append(raw_fallback if raw_fallback is not None else pattern)
        return
    if len(matches) > MAX_FILES_PER_PATTERN:
        truncated.append(
            TruncatedPattern(
                pattern=pattern,
                shown=MAX_FILES_PER_PATTERN,
                total=len(matches),
            )
        )
        matches = matches[:MAX_FILES_PER_PATTERN]
    for path in matches:
        if path not in seen:
            seen.add(path)
            selected.append(SelectedFile(path=path, from_pattern=True))
