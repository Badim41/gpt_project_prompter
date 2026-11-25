from __future__ import annotations

import json
from pathlib import Path

from gpt_project_prompter_core.constants import (
    MAX_JSON_BYTES,
    MAX_JSON_DEPTH,
    MAX_JSON_KEYS,
    MAX_JSON_SIGNATURE_CHARS,
)
from gpt_project_prompter_core.extractors.base import SymbolOutline


class JsonSignatureExtractor:
    """Формирует компактную структурную сигнатуру для JSON-файлов."""

    def supports(self, rel_path: str) -> bool:
        """Принимает .json файлы, кроме package-lock.json."""
        p = Path(rel_path)
        return p.suffix.lower() == ".json" and p.name.lower() != "package-lock.json"

    def extract(
        self,
        data: bytes,
        rel_path: str,
        local_modules: frozenset[str],
    ) -> SymbolOutline | None:
        """Разбирает JSON до 64 КБ и возвращает ключи (L2) и полную схему (L3)."""
        _ = local_modules
        if len(data) > MAX_JSON_BYTES or not self.supports(rel_path):
            return None
        try:
            loaded: object = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return None

        compact_line = _format_compact_root(loaded)
        detailed_line = _clip_signature(_format_value(loaded, depth=0))
        return SymbolOutline(compact_lines=(compact_line,), detailed_lines=(detailed_line,))


def _format_compact_root(value: object) -> str:
    if isinstance(value, dict):
        keys = [str(k) for k in value.keys()]
        if not keys:
            return "{}"
        shown = keys[:MAX_JSON_KEYS]
        extra = len(keys) - len(shown)
        suffix = f", …+{extra}" if extra > 0 else ""
        return _clip_signature(f"keys: {', '.join(shown)}{suffix}")
    return _clip_signature(_format_value(value, depth=0))


def _format_value(value: object, depth: int) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return _format_list(value, depth)
    if isinstance(value, dict):
        return _format_dict(value, depth)
    return "any"


def _format_dict(obj: dict[object, object], depth: int) -> str:
    count = len(obj)
    if count == 0:
        return "{}"
    if depth >= MAX_JSON_DEPTH:
        return f"{{…{count} ключей}}"
    items = list(obj.items())
    shown = items[:MAX_JSON_KEYS]
    parts = [f"{k}: {_format_value(v, depth + 1)}" for k, v in shown]
    if count > MAX_JSON_KEYS:
        parts.append(f"…+{count - MAX_JSON_KEYS}")
    return "{" + ", ".join(parts) + "}"


def _format_list(arr: list[object], depth: int) -> str:
    length = len(arr)
    if length == 0:
        return "[]"
    first_sig = _format_value(arr[0], depth + 1)
    for item in arr[1:]:
        if _format_value(item, depth + 1) != first_sig:
            return f"[{length} × mixed]"
    return f"[{length} × {first_sig}]"


def _clip_signature(text: str) -> str:
    if len(text) <= MAX_JSON_SIGNATURE_CHARS:
        return text
    return text[: MAX_JSON_SIGNATURE_CHARS - 1] + "…"
