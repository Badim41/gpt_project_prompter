from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from gpt_project_prompter_core.constants import (
    JS_TS_EXTENSIONS,
    MAX_JS_TS_BYTES,
    MAX_SIGNATURE_CHARS,
)
from gpt_project_prompter_core.extractors.base import SymbolOutline

_CONTROL_WORDS: frozenset[str] = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "else",
        "return",
        "new",
        "typeof",
        "delete",
        "throw",
        "await",
        "do",
        "try",
        "case",
        "super",
    }
)
_REGEX_PREV_CHARS: frozenset[str] = frozenset("=(,[!:;&|?{}\n;")

_CLASS_RE = re.compile(
    r"^(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"
    r"(?:\s*<[^>]*>)?(?:\s+extends\s+([A-Za-z_$][\w$.<>]*))?"
    r"(?:\s+implements\s+([A-Za-z_$][\w$.<>,\s]*))?\s*\{"
)
_FUNC_RE = re.compile(
    r"^(?:(export\s+(?:default\s+)?))?(?:(async)\s+)?function\s*(\*)?\s*([A-Za-z_$][\w$]*)?"
    r"(?:\s*<[^>]*>)?\s*\("
)
_VAR_FUNC_RE = re.compile(
    r"^(?:(export)\s+)?(const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:(async)\s+)?"
    r"(?:(function\s*\*?)\s*)?\("
)
_VAR_SINGLE_ARROW_RE = re.compile(
    r"^(?:(export)\s+)?(const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:(async)\s+)?"
    r"([A-Za-z_$][\w$]*)\s*=>"
)
_UPPER_CONST_RE = re.compile(
    r"^(?:export\s+)?const\s+([A-Z][A-Z0-9_]+)\s*(?::[^=]+)?="
)
_TS_DECL_RE = re.compile(
    r"^(?:(export)\s+)?(?:(declare)\s+)?(interface|type|enum)\s+([A-Za-z_$][\w$]*)"
)
_METHOD_RE = re.compile(
    r"^(?:(?:public|private|protected|override|readonly)\s+)*"
    r"(?:(static)\s+)?(?:(async)\s+)?(?:(get|set)\s+)?(\*)?\s*"
    r"([A-Za-z_$#][\w$]*)\s*(?:<[^>]*>)?\s*\("
)


@dataclass(frozen=True, slots=True)
class _JsClass:
    name: str
    header: str
    methods: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _JsSymbols:
    classes: tuple[_JsClass, ...]
    functions: tuple[tuple[str, str], ...]
    types: tuple[tuple[str, str], ...]
    constants: tuple[str, ...]


class JsTsExtractor:
    """Лёгкий сканер JS/TS верхнего уровня и тел классов без внешних парсеров."""

    def supports(self, rel_path: str) -> bool:
        """Принимает JS/TS файлы, кроме .min.js и .d.ts."""
        lower = rel_path.lower()
        if lower.endswith(".min.js") or lower.endswith(".d.ts"):
            return False
        return Path(lower).suffix in JS_TS_EXTENSIONS

    def extract(
        self,
        data: bytes,
        rel_path: str,
        local_modules: frozenset[str],
    ) -> SymbolOutline | None:
        """Строит контур JS/TS для уровней 2 и 3."""
        _ = local_modules
        if len(data) > MAX_JS_TS_BYTES or not self.supports(rel_path):
            return None
        try:
            source = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None

        cleaned = _strip_comments_and_strings(source)
        symbols = _parse_top_level(cleaned)
        compact = _render_compact(symbols)
        detailed = _render_detailed(symbols)
        if not compact and not detailed:
            return None
        return SymbolOutline(compact_lines=tuple(compact), detailed_lines=tuple(detailed))


def _strip_comments_and_strings(source: str) -> str:
    out: list[str] = []
    i = 0
    n = len(source)
    last_sig = "\n"

    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            i = _skip_line_comment(source, i, out)
        elif ch == "/" and nxt == "*":
            i = _skip_block_comment(source, i, out)
        elif ch in ("'", '"'):
            i = _skip_quoted(source, i, ch, out)
            last_sig = ch
        elif ch == "`":
            i = _skip_template(source, i, out)
            last_sig = "`"
        elif ch == "/" and last_sig in _REGEX_PREV_CHARS:
            i = _skip_regex(source, i, out)
            last_sig = "/"
        else:
            out.append(ch)
            if not ch.isspace():
                last_sig = ch
            i += 1
    return "".join(out)


def _skip_line_comment(source: str, i: int, out: list[str]) -> int:
    n = len(source)
    while i < n and source[i] != "\n":
        out.append(" ")
        i += 1
    return i


def _skip_block_comment(source: str, i: int, out: list[str]) -> int:
    n = len(source)
    out.extend((" ", " "))
    i += 2
    while i < n:
        if source[i] == "*" and i + 1 < n and source[i + 1] == "/":
            out.extend((" ", " "))
            return i + 2
        out.append("\n" if source[i] == "\n" else " ")
        i += 1
    return i


def _skip_quoted(source: str, i: int, quote: str, out: list[str]) -> int:
    n = len(source)
    out.append(quote)
    i += 1
    while i < n:
        ch = source[i]
        if ch == "\\" and i + 1 < n:
            out.extend((" ", "\n" if source[i + 1] == "\n" else " "))
            i += 2
            continue
        if ch == quote:
            out.append(quote)
            return i + 1
        out.append("\n" if ch == "\n" else " ")
        i += 1
    return i


def _skip_template(source: str, i: int, out: list[str]) -> int:
    n = len(source)
    out.append("`")
    i += 1
    expr_depth = 0
    while i < n:
        ch = source[i]
        if ch == "\\" and i + 1 < n:
            out.extend((" ", "\n" if source[i + 1] == "\n" else " "))
            i += 2
            continue
        if expr_depth == 0:
            if ch == "`":
                out.append("`")
                return i + 1
            if ch == "$" and i + 1 < n and source[i + 1] == "{":
                expr_depth = 1
                out.extend((" ", " "))
                i += 2
                continue
        else:
            if ch == "{":
                expr_depth += 1
            elif ch == "}":
                expr_depth -= 1
        out.append("\n" if ch == "\n" else " ")
        i += 1
    return i


def _skip_regex(source: str, i: int, out: list[str]) -> int:
    n = len(source)
    out.append("/")
    i += 1
    in_class = False
    while i < n and source[i] != "\n":
        ch = source[i]
        if ch == "\\" and i + 1 < n:
            out.extend((" ", " "))
            i += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            out.append("/")
            return i + 1
        out.append(" ")
        i += 1
    return i


def _find_balanced(text: str, open_idx: int, open_ch: str, close_ch: str) -> int | None:
    depth = 0
    for idx in range(open_idx, len(text)):
        ch = text[idx]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return idx
    return None


def _extract_return_type(tail: str) -> str:
    stripped = tail.lstrip()
    if not stripped.startswith(":"):
        return ""
    body = stripped[1:]
    angle = 0
    paren = 0
    for idx, ch in enumerate(body):
        if ch == "<":
            angle += 1
        elif ch == ">" and angle > 0:
            angle -= 1
        elif ch == "(":
            paren += 1
        elif ch == ")" and paren > 0:
            paren -= 1
        elif angle == 0 and paren == 0:
            if ch in "{;\n" or body.startswith("=>", idx):
                ret = _collapse_ws(body[:idx])
                return f": {ret}" if ret else ""
    return ""


def _collapse_ws(text: str) -> str:
    return " ".join(text.split())


def _clip_sig(sig: str) -> str:
    return sig if len(sig) <= MAX_SIGNATURE_CHARS else sig[: MAX_SIGNATURE_CHARS - 1] + "…"


def _parse_top_level(cleaned: str) -> _JsSymbols:
    classes: list[_JsClass] = []
    functions: list[tuple[str, str]] = []
    types: list[tuple[str, str]] = []
    constants: list[str] = []

    i = 0
    n = len(cleaned)
    while i < n:
        while i < n and cleaned[i] in " \t\r\n;":
            i += 1
        if i >= n:
            break
        if cleaned[i] == "{":
            end_b = _find_balanced(cleaned, i, "{", "}")
            i = (end_b + 1) if end_b is not None else n
            continue

        slice_text = cleaned[i:]
        cls_res = _try_parse_class(slice_text, i, cleaned)
        if cls_res is not None:
            cls_obj, i = cls_res
            classes.append(cls_obj)
            continue

        fn_res = _try_parse_function(slice_text, i, cleaned)
        if fn_res is not None:
            fn_item, i = fn_res
            functions.append(fn_item)
            continue

        type_res = _try_parse_ts_type(slice_text, i, cleaned)
        if type_res is not None:
            t_item, i = type_res
            types.append(t_item)
            continue

        var_res = _try_parse_var_decl(slice_text, i, cleaned)
        if var_res is not None:
            kind, item, i = var_res
            if kind == "fn" and isinstance(item, tuple):
                functions.append(item)
            elif kind == "const" and isinstance(item, str) and item not in constants:
                constants.append(item)
            continue

        i = _skip_statement(cleaned, i)

    return _JsSymbols(
        classes=tuple(classes),
        functions=tuple(functions),
        types=tuple(types),
        constants=tuple(constants),
    )


def _try_parse_class(
    slice_text: str,
    offset: int,
    full: str,
) -> tuple[_JsClass, int] | None:
    match = _CLASS_RE.match(slice_text)
    if match is None:
        return None
    name = match.group(1)
    brace_idx = offset + match.end() - 1
    end_brace = _find_balanced(full, brace_idx, "{", "}")
    if end_brace is None:
        return None
    header = _collapse_ws(slice_text[: match.end() - 1])
    methods = _parse_class_body(full[brace_idx + 1 : end_brace])
    return _JsClass(name=name, header=header, methods=tuple(methods)), end_brace + 1


def _try_parse_function(
    slice_text: str,
    offset: int,
    full: str,
) -> tuple[tuple[str, str], int] | None:
    match = _FUNC_RE.match(slice_text)
    if match is None:
        return None
    exp, async_kw, star, name = match.groups()
    fn_name = name or "default"
    open_paren = offset + match.end() - 1
    close_paren = _find_balanced(full, open_paren, "(", ")")
    if close_paren is None:
        return None
    params = _collapse_ws(full[open_paren + 1 : close_paren])
    ret = _extract_return_type(full[close_paren + 1 : close_paren + 200])
    prefix = f"{_collapse_ws(exp)} " if exp else ""
    async_part = "async " if async_kw else ""
    star_part = "*" if star else ""
    sig = _clip_sig(f"{prefix}{async_part}function{star_part} {fn_name}({params}){ret}")
    next_i = _skip_after_signature(full, close_paren + 1)
    return (fn_name, sig), next_i


def _try_parse_ts_type(
    slice_text: str,
    offset: int,
    full: str,
) -> tuple[tuple[str, str], int] | None:
    match = _TS_DECL_RE.match(slice_text)
    if match is None:
        return None
    exp, _, kind, name = match.groups()
    if kind == "type":
        after = slice_text[match.end() :]
        if "=" not in after.split("\n", 1)[0]:
            return None
    prefix = "export " if exp else ""
    label = f"{prefix}{kind} {name}"
    next_i = _skip_statement(full, offset + match.end())
    return (f"{kind} {name}", label), next_i


def _try_parse_var_decl(
    slice_text: str,
    offset: int,
    full: str,
) -> tuple[str, tuple[str, str] | str, int] | None:
    match = _VAR_FUNC_RE.match(slice_text)
    if match is not None:
        exp, _, name, async_kw, fn_kw = match.groups()
        open_paren = offset + match.end() - 1
        close_paren = _find_balanced(full, open_paren, "(", ")")
        if close_paren is not None:
            after = full[close_paren + 1 : close_paren + 240]
            if fn_kw or "=>" in after.split("\n", 2)[0] or "=>" in after:
                params = _collapse_ws(full[open_paren + 1 : close_paren])
                ret = _extract_return_type(after)
                prefix = "export " if exp else ""
                async_part = "async " if async_kw else ""
                sig = _clip_sig(f"{prefix}{async_part}{name}({params}){ret}")
                return "fn", (name, sig), _skip_statement(full, close_paren + 1)

    single = _VAR_SINGLE_ARROW_RE.match(slice_text)
    if single is not None:
        exp, _, name, async_kw, param = single.groups()
        prefix = "export " if exp else ""
        async_part = "async " if async_kw else ""
        sig = _clip_sig(f"{prefix}{async_part}{name}({param})")
        return "fn", (name, sig), _skip_statement(full, offset + single.end())

    const_m = _UPPER_CONST_RE.match(slice_text)
    if const_m is not None:
        return "const", const_m.group(1), _skip_statement(full, offset + const_m.end())
    return None


def _parse_class_body(body: str) -> list[tuple[str, str]]:
    methods: list[tuple[str, str]] = []
    i = 0
    n = len(body)
    while i < n:
        while i < n and body[i] in " \t\r\n;":
            i += 1
        if i >= n:
            break
        if body[i] == "{":
            end_b = _find_balanced(body, i, "{", "}")
            i = (end_b + 1) if end_b is not None else n
            continue

        match = _METHOD_RE.match(body[i:])
        if match is not None:
            static_kw, async_kw, accessor, star, name = match.groups()
            if name not in _CONTROL_WORDS:
                open_p = i + match.end() - 1
                close_p = _find_balanced(body, open_p, "(", ")")
                if close_p is not None and _looks_like_method_tail(body[close_p + 1 :]):
                    params = _collapse_ws(body[open_p + 1 : close_p])
                    ret = _extract_return_type(body[close_p + 1 : close_p + 160])
                    mods = [m for m in (static_kw, async_kw, accessor) if m]
                    prefix = (" ".join(mods) + " ") if mods else ""
                    star_p = "*" if star else ""
                    sig = _clip_sig(f"{prefix}{star_p}{name}({params}){ret}")
                    methods.append((name, sig))
                    i = _skip_after_signature(body, close_p + 1)
                    continue
        i = _skip_statement(body, i)
    return methods


def _looks_like_method_tail(tail: str) -> bool:
    stripped = tail.lstrip()
    if stripped.startswith("{"):
        return True
    if stripped.startswith(":"):
        brace_pos = stripped.find("{")
        semi_pos = stripped.find(";")
        nl_pos = stripped.find("\n")
        candidates = [p for p in (brace_pos, semi_pos) if p != -1]
        if not candidates:
            return False
        first = min(candidates)
        return nl_pos == -1 or first <= nl_pos + 80
    return False


def _skip_after_signature(text: str, pos: int) -> int:
    n = len(text)
    while pos < n:
        ch = text[pos]
        if ch == "{":
            end_b = _find_balanced(text, pos, "{", "}")
            return (end_b + 1) if end_b is not None else n
        if ch == ";":
            return pos + 1
        pos += 1
    return n


def _skip_statement(text: str, pos: int) -> int:
    n = len(text)
    while pos < n:
        ch = text[pos]
        if ch == "{":
            end_b = _find_balanced(text, pos, "{", "}")
            return (end_b + 1) if end_b is not None else n
        if ch in ";\n":
            return pos + 1
        pos += 1
    return n


def _render_compact(symbols: _JsSymbols) -> list[str]:
    lines: list[str] = []
    if symbols.constants:
        lines.append(f"const {', '.join(symbols.constants)}")
    for kind_name, _ in symbols.types:
        lines.append(kind_name)
    for cls in symbols.classes:
        names = [m_name for m_name, _ in cls.methods]
        lines.append(f"class {cls.name}: {', '.join(names)}" if names else f"class {cls.name}")
    if symbols.functions:
        fn_names = [name for name, _ in symbols.functions]
        lines.append(f"fn {', '.join(fn_names)}")
    return lines


def _render_detailed(symbols: _JsSymbols) -> list[str]:
    lines: list[str] = []
    if symbols.constants:
        lines.append(f"const {', '.join(symbols.constants)}")
    for _, full_type in symbols.types:
        lines.append(full_type)
    for cls in symbols.classes:
        lines.append(cls.header)
        for _, sig in cls.methods:
            lines.append(f"  {sig}")
    for _, sig in symbols.functions:
        lines.append(sig)
    return lines
