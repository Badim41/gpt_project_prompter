from __future__ import annotations

import ast
from pathlib import Path
import re

from gpt_project_prompter_core.constants import (
    MAX_CLASS_ATTRS,
    MAX_DOCSTRING_CHARS,
    MAX_LOCAL_IMPORTS,
    MAX_PYTHON_AST_BYTES,
    MAX_SIGNATURE_CHARS,
    PYTHON_EXTENSIONS,
)
from gpt_project_prompter_core.extractors.base import SymbolOutline

_ALLOWED_DECORATORS: frozenset[str] = frozenset(
    {"property", "staticmethod", "classmethod", "abstractmethod", "dataclass"}
)
_UPPER_CONST_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")


class PythonAstExtractor:
    """Извлекает контур Python-файла через stdlib ast."""

    def supports(self, rel_path: str) -> bool:
        """Проверяет, является ли файл модулем Python."""
        return Path(rel_path).suffix.lower() in PYTHON_EXTENSIONS

    def extract(
        self,
        data: bytes,
        rel_path: str,
        local_modules: frozenset[str],
    ) -> SymbolOutline | None:
        """Разбирает AST и формирует компактный (L2) и детальный (L3) контуры."""
        if len(data) > MAX_PYTHON_AST_BYTES:
            return None
        try:
            source = data.decode("utf-8-sig")
            tree = ast.parse(source)
        except (UnicodeDecodeError, SyntaxError, ValueError, RecursionError, MemoryError):
            return None

        compact = _build_compact_lines(tree)
        detailed = _build_detailed_lines(tree, rel_path, local_modules)
        if not compact and not detailed:
            return None
        return SymbolOutline(compact_lines=tuple(compact), detailed_lines=tuple(detailed))


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _is_kept_in_detailed(name: str) -> bool:
    if name == "__init__":
        return True
    return not (name.startswith("__") and name.endswith("__"))


def _first_doc_line(node: ast.AST) -> str | None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
        return None
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return None
    first = doc.splitlines()[0].strip()
    if not first:
        return None
    if len(first) > MAX_DOCSTRING_CHARS:
        return first[: MAX_DOCSTRING_CHARS - 1] + "…"
    return first


def _decorator_name(dec: ast.expr) -> str | None:
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _dataclass_label(decorators: list[ast.expr]) -> str | None:
    for dec in decorators:
        if _decorator_name(dec) != "dataclass":
            continue
        if isinstance(dec, ast.Call):
            for kw in dec.keywords:
                if kw.arg == "frozen" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    return "frozen dataclass"
        return "dataclass"
    return None


def _filtered_func_decorators(decorators: list[ast.expr]) -> list[str]:
    kept: list[str] = []
    for dec in decorators:
        name = _decorator_name(dec)
        if name in _ALLOWED_DECORATORS and name != "dataclass":
            kept.append(f"@{name}")
    return kept


def _extract_top_constants(tree: ast.Module) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for node in tree.body:
        targets: list[str] = []
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    targets.append(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets.append(node.target.id)
        for name in targets:
            if _UPPER_CONST_RE.match(name) and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _extract_local_imports(
    tree: ast.Module,
    rel_path: str,
    local_modules: frozenset[str],
) -> list[str]:
    pkg_prefix = _current_top_package(rel_path)
    found: list[str] = []
    seen: set[str] = set()

    for node in tree.body:
        candidates = _import_node_modules(node, local_modules, pkg_prefix)
        for mod in candidates:
            if mod not in seen:
                seen.add(mod)
                found.append(mod)
                if len(found) >= MAX_LOCAL_IMPORTS:
                    return found
    return found


def _current_top_package(rel_path: str) -> str | None:
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] == "src":
        return parts[1] if len(parts) >= 3 else None
    return parts[0] if len(parts) >= 2 else None


def _import_node_modules(
    node: ast.stmt,
    local_modules: frozenset[str],
    pkg_prefix: str | None,
) -> list[str]:
    result: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            first = alias.name.split(".", 1)[0]
            if first in local_modules:
                result.append(_shorten_local(alias.name, pkg_prefix))
    elif isinstance(node, ast.ImportFrom):
        if node.level > 0:
            if node.module:
                result.append(node.module)
            else:
                result.extend(alias.name for alias in node.names if alias.name != "*")
        elif node.module:
            first = node.module.split(".", 1)[0]
            if first in local_modules:
                result.append(_shorten_local(node.module, pkg_prefix))
    return result


def _shorten_local(module_name: str, pkg_prefix: str | None) -> str:
    if pkg_prefix and module_name.startswith(f"{pkg_prefix}."):
        return module_name[len(pkg_prefix) + 1 :]
    return module_name


def _format_arg(arg: ast.arg, has_default: bool) -> str:
    part = f"{arg.arg}: {ast.unparse(arg.annotation)}" if arg.annotation is not None else arg.arg
    return f"{part}=…" if has_default else part


def _format_arguments(args: ast.arguments) -> str:
    positional = list(args.posonlyargs) + list(args.args)
    default_offset = len(positional) - len(args.defaults)
    parts: list[str] = []

    for idx, arg in enumerate(args.posonlyargs):
        parts.append(_format_arg(arg, idx >= default_offset))
    if args.posonlyargs:
        parts.append("/")

    offset = len(args.posonlyargs)
    for idx, arg in enumerate(args.args):
        parts.append(_format_arg(arg, (offset + idx) >= default_offset))

    if args.vararg is not None:
        parts.append("*" + _format_arg(args.vararg, False))
    elif args.kwonlyargs:
        parts.append("*")

    for kw_arg, kw_def in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(_format_arg(kw_arg, kw_def is not None))

    if args.kwarg is not None:
        parts.append("**" + _format_arg(args.kwarg, False))
    return ", ".join(parts)


def _format_func_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    is_method: bool,
) -> str:
    decs = _filtered_func_decorators(node.decorator_list)
    prefix_parts = list(decs)
    if isinstance(node, ast.AsyncFunctionDef):
        prefix_parts.append("async")
    if not is_method:
        prefix_parts.append("def")
    prefix = (" ".join(prefix_parts) + " ") if prefix_parts else ""
    params = _format_arguments(node.args)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    sig = f"{prefix}{node.name}({params}){ret}"
    if len(sig) > MAX_SIGNATURE_CHARS:
        sig = sig[: MAX_SIGNATURE_CHARS - 1] + "…"
    doc = _first_doc_line(node)
    return f"{sig}  # {doc}" if doc else sig


def _format_class_header(node: ast.ClassDef) -> str:
    dc_label = _dataclass_label(node.decorator_list)
    bases = [ast.unparse(base) for base in node.bases]
    header_items: list[str] = []
    if dc_label:
        header_items.append(dc_label)
    header_items.extend(bases)
    suffix = f"({', '.join(header_items)})" if header_items else ""
    header = f"class {node.name}{suffix}"
    doc = _first_doc_line(node)
    return f"{header}  # {doc}" if doc else header


def _build_compact_lines(tree: ast.Module) -> list[str]:
    lines: list[str] = []
    consts = [c for c in _extract_top_constants(tree) if _is_public(c)]
    if consts:
        lines.append(f"const {', '.join(consts)}")

    funcs: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_public(node.name):
            lines.append(_compact_class_line(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(node.name):
            label = f"async {node.name}" if isinstance(node, ast.AsyncFunctionDef) else node.name
            funcs.append(label)

    if funcs:
        lines.append(f"def {', '.join(funcs)}")
    return lines


def _compact_class_line(node: ast.ClassDef) -> str:
    members: list[str] = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(item.name):
            members.append(item.name)
        elif isinstance(item, ast.ClassDef) and _is_public(item.name):
            members.append(f"class {item.name}")
    if members:
        return f"class {node.name}: {', '.join(members)}"
    return f"class {node.name}"


def _build_detailed_lines(
    tree: ast.Module,
    rel_path: str,
    local_modules: frozenset[str],
) -> list[str]:
    lines: list[str] = []
    imports = _extract_local_imports(tree, rel_path, local_modules)
    if imports:
        lines.append(f"imports: {', '.join(imports)}")

    consts = _extract_top_constants(tree)
    if consts:
        lines.append(f"const {', '.join(consts)}")

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_kept_in_detailed(node.name):
            lines.extend(_detailed_class_lines(node, indent="", allow_nested=True))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_kept_in_detailed(node.name):
            lines.append(_format_func_signature(node, is_method=False))
    return lines


def _detailed_class_lines(
    node: ast.ClassDef,
    indent: str,
    *,
    allow_nested: bool,
) -> list[str]:
    lines: list[str] = [f"{indent}{_format_class_header(node)}"]
    child_indent = indent + "  "
    attr_count = 0

    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            if attr_count < MAX_CLASS_ATTRS and _is_kept_in_detailed(item.target.id):
                ann = ast.unparse(item.annotation)
                lines.append(f"{child_indent}{item.target.id}: {ann}")
                attr_count += 1
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_kept_in_detailed(item.name):
                lines.append(f"{child_indent}{_format_func_signature(item, is_method=True)}")
        elif allow_nested and isinstance(item, ast.ClassDef) and _is_kept_in_detailed(item.name):
            lines.extend(_detailed_class_lines(item, indent=child_indent, allow_nested=False))
    return lines
