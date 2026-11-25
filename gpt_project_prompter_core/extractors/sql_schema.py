from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

from gpt_project_prompter_core.constants import (
    MAX_SQL_BYTES,
    MAX_SQL_COLUMNS,
    MAX_SQL_TABLES,
)
from gpt_project_prompter_core.extractors.base import SymbolOutline

_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+(?:(?:TEMP|TEMPORARY)\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([\"`\[\w.\]]+)\s*\(",
    re.IGNORECASE,
)
_CREATE_VIEW_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:TEMP|TEMPORARY)\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([\"`\[\w.\]]+)",
    re.IGNORECASE,
)
_TABLE_PK_RE = re.compile(
    r"^(?:CONSTRAINT\s+\S+\s+)?PRIMARY\s+KEY\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_TABLE_UQ_RE = re.compile(
    r"^(?:CONSTRAINT\s+\S+\s+)?UNIQUE\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_TABLE_FK_RE = re.compile(
    r"^(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+"
    r"([\"`\[\w.\]]+)(?:\s*\(([^)]+)\))?",
    re.IGNORECASE,
)
_INLINE_REF_RE = re.compile(
    r"\bREFERENCES\s+([\"`\[\w.\]]+)(?:\s*\(([^)]+)\))?",
    re.IGNORECASE,
)
_CONSTRAINT_STOP_WORDS: frozenset[str] = frozenset(
    {
        "PRIMARY",
        "NOT",
        "NULL",
        "UNIQUE",
        "DEFAULT",
        "REFERENCES",
        "CHECK",
        "GENERATED",
        "COLLATE",
        "CONSTRAINT",
        "AUTO_INCREMENT",
        "AUTOINCREMENT",
    }
)
_TABLE_CONSTRAINT_STARTS: frozenset[str] = frozenset(
    {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX", "KEY"}
)


@dataclass
class _SqlColumn:
    name: str
    col_type: str
    tags: list[str]


@dataclass(frozen=True, slots=True)
class _SqlTable:
    name: str
    columns: tuple[str, ...]


class SqlSchemaExtractor:
    """Извлекает схему таблиц и представлений из .sql файлов."""

    def supports(self, rel_path: str) -> bool:
        """Проверяет расширение .sql."""
        return Path(rel_path).suffix.lower() == ".sql"

    def extract(
        self,
        data: bytes,
        rel_path: str,
        local_modules: frozenset[str],
    ) -> SymbolOutline | None:
        """Разбирает CREATE TABLE и CREATE VIEW из .sql до 256 КБ."""
        _ = local_modules
        if len(data) > MAX_SQL_BYTES or not self.supports(rel_path):
            return None
        try:
            source = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None

        cleaned = _strip_sql_comments(source)
        tables = _parse_sql_tables(cleaned)
        views = _parse_sql_views(cleaned)
        return _format_schema_outline(tables, views)


def extract_sqlite_schema(file_path: Path) -> SymbolOutline | None:
    """Читает схему SQLite-файла строго в режиме read-only."""
    uri = f"{file_path.resolve().as_uri()}?mode=ro"
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name, type FROM sqlite_master "
                "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name;"
            )
            rows = cur.fetchall()
            tables: list[_SqlTable] = []
            views: list[str] = []
            for obj_name, obj_type in rows:
                name_str = str(obj_name)
                if obj_type == "view":
                    views.append(name_str)
                elif len(tables) < MAX_SQL_TABLES:
                    tables.append(_read_sqlite_table(cur, name_str))
            return _format_schema_outline(tables, views)
    except (sqlite3.Error, OSError, ValueError):
        return None


def _read_sqlite_table(cur: sqlite3.Cursor, table_name: str) -> _SqlTable:
    quoted = table_name.replace('"', '""')
    fk_map = _read_sqlite_fks(cur, quoted)
    uq_cols = _read_sqlite_unique_cols(cur, quoted)

    cur.execute(f'PRAGMA table_info("{quoted}");')
    formatted: list[str] = []
    for row in cur.fetchall()[:MAX_SQL_COLUMNS]:
        col_name = str(row[1])
        col_type = str(row[2] or "").strip()
        tags: list[str] = []
        if row[5]:
            tags.append("PK")
        if row[3]:
            tags.append("NN")
        if col_name.lower() in uq_cols and "PK" not in tags:
            tags.append("UQ")
        for fk_tag in fk_map.get(col_name.lower(), ()):
            tags.append(fk_tag)
        formatted.append(_format_col_entry(col_name, col_type, tags))
    return _SqlTable(name=table_name, columns=tuple(formatted))


def _read_sqlite_fks(cur: sqlite3.Cursor, quoted_table: str) -> dict[str, list[str]]:
    fk_map: dict[str, list[str]] = {}
    cur.execute(f'PRAGMA foreign_key_list("{quoted_table}");')
    for row in cur.fetchall():
        ref_table = str(row[2])
        from_col = str(row[3]).lower()
        to_col = str(row[4]) if row[4] else ""
        tag = f"FK->{ref_table}.{to_col}" if to_col else f"FK->{ref_table}"
        fk_map.setdefault(from_col, []).append(tag)
    return fk_map


def _read_sqlite_unique_cols(cur: sqlite3.Cursor, quoted_table: str) -> set[str]:
    unique_cols: set[str] = set()
    cur.execute(f'PRAGMA index_list("{quoted_table}");')
    for idx_row in cur.fetchall():
        if not idx_row[2]:
            continue
        idx_name = str(idx_row[1]).replace('"', '""')
        cur.execute(f'PRAGMA index_info("{idx_name}");')
        info_rows = cur.fetchall()
        if len(info_rows) == 1 and info_rows[0][2]:
            unique_cols.add(str(info_rows[0][2]).lower())
    return unique_cols


def _strip_sql_comments(sql: str) -> str:
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch in ("'", '"', "`"):
            i = _copy_sql_quoted(sql, i, ch, out)
        elif ch == "[":
            i = _copy_sql_quoted(sql, i, "]", out)
        elif ch == "-" and nxt == "-":
            while i < n and sql[i] != "\n":
                i += 1
        elif ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _copy_sql_quoted(sql: str, i: int, end_ch: str, out: list[str]) -> int:
    n = len(sql)
    out.append(sql[i])
    i += 1
    while i < n:
        ch = sql[i]
        out.append(ch)
        if ch == end_ch:
            if end_ch != "]" and i + 1 < n and sql[i + 1] == end_ch:
                out.append(sql[i + 1])
                i += 2
                continue
            return i + 1
        i += 1
    return i


def _unquote_ident(raw: str) -> str:
    parts = raw.strip().split(".")
    cleaned: list[str] = []
    for part in parts:
        p = part.strip()
        if len(p) >= 2 and (
            (p[0] == '"' and p[-1] == '"')
            or (p[0] == "`" and p[-1] == "`")
            or (p[0] == "[" and p[-1] == "]")
        ):
            p = p[1:-1]
        cleaned.append(p)
    return ".".join(cleaned)


def _find_matching_paren(text: str, open_idx: int) -> int | None:
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"', "`"):
            end_q = _find_quote_end(text, i, ch)
            i = end_q
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _find_quote_end(text: str, start: int, quote: str) -> int:
    i = start + 1
    n = len(text)
    while i < n:
        if text[i] == quote:
            if i + 1 < n and text[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return n


def _split_top_level_commas(body: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch in ("'", '"', "`"):
            end_q = _find_quote_end(body, i, ch)
            buf.append(body[i:end_q])
            i = end_q
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            item = "".join(buf).strip()
            if item:
                parts.append(item)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_sql_tables(cleaned: str) -> list[_SqlTable]:
    tables: list[_SqlTable] = []
    pos = 0
    while len(tables) < MAX_SQL_TABLES:
        match = _CREATE_TABLE_RE.search(cleaned, pos)
        if match is None:
            break
        table_name = _unquote_ident(match.group(1))
        open_idx = match.end() - 1
        close_idx = _find_matching_paren(cleaned, open_idx)
        if close_idx is None:
            break
        cols = _parse_table_columns(cleaned[open_idx + 1 : close_idx])
        tables.append(_SqlTable(name=table_name, columns=tuple(cols)))
        pos = close_idx + 1
    return tables


def _parse_sql_views(cleaned: str) -> list[str]:
    views: list[str] = []
    seen: set[str] = set()
    for match in _CREATE_VIEW_RE.finditer(cleaned):
        name = _unquote_ident(match.group(1))
        if name not in seen:
            seen.add(name)
            views.append(name)
    return views


def _parse_table_columns(body: str) -> list[str]:
    defs = _split_top_level_commas(body)
    columns: list[_SqlColumn] = []
    constraints: list[str] = []

    for entry in defs:
        first_word = entry.split(None, 1)[0].upper() if entry.split() else ""
        if first_word in _TABLE_CONSTRAINT_STARTS:
            constraints.append(entry)
        elif len(columns) < MAX_SQL_COLUMNS:
            col = _parse_column_def(entry)
            if col is not None:
                columns.append(col)

    _apply_table_constraints(columns, constraints)
    return [_format_col_entry(c.name, c.col_type, c.tags) for c in columns]


def _parse_column_def(entry: str) -> _SqlColumn | None:
    tokens = _tokenize_col_head(entry)
    if not tokens:
        return None
    col_name = _unquote_ident(tokens[0])
    type_parts: list[str] = []
    idx = 1
    while idx < len(tokens):
        upper = tokens[idx].upper()
        if upper in _CONSTRAINT_STOP_WORDS:
            break
        type_parts.append(tokens[idx])
        idx += 1
    col_type = " ".join(type_parts)
    rest = entry[len(tokens[0]) :]
    tags = _extract_inline_tags(rest)
    return _SqlColumn(name=col_name, col_type=col_type, tags=tags)


def _tokenize_col_head(entry: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    n = len(entry)
    while i < n:
        while i < n and entry[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        if entry[i] in ('"', "`", "["):
            end_c = "]" if entry[i] == "[" else entry[i]
            i += 1
            while i < n and entry[i] != end_c:
                i += 1
            i = min(n, i + 1)
        else:
            depth = 0
            while i < n and (depth > 0 or not entry[i].isspace()):
                if entry[i] == "(":
                    depth += 1
                elif entry[i] == ")" and depth > 0:
                    depth -= 1
                i += 1
        tokens.append(entry[start:i])
    return tokens


def _extract_inline_tags(rest: str) -> list[str]:
    tags: list[str] = []
    if re.search(r"\bPRIMARY\s+KEY\b", rest, re.IGNORECASE):
        tags.append("PK")
    if re.search(r"\bNOT\s+NULL\b", rest, re.IGNORECASE):
        tags.append("NN")
    if re.search(r"\bUNIQUE\b", rest, re.IGNORECASE):
        tags.append("UQ")
    ref_m = _INLINE_REF_RE.search(rest)
    if ref_m is not None:
        ref_tbl = _unquote_ident(ref_m.group(1))
        ref_col = _unquote_ident(ref_m.group(2).split(",")[0]) if ref_m.group(2) else ""
        tags.append(f"FK->{ref_tbl}.{ref_col}" if ref_col else f"FK->{ref_tbl}")
    return tags


def _apply_table_constraints(columns: list[_SqlColumn], constraints: list[str]) -> None:
    by_lower = {c.name.lower(): c for c in columns}
    for constr in constraints:
        pk_m = _TABLE_PK_RE.match(constr)
        if pk_m is not None:
            for col in _parse_ident_list(pk_m.group(1)):
                _add_tag(by_lower.get(col.lower()), "PK")
            continue
        uq_m = _TABLE_UQ_RE.match(constr)
        if uq_m is not None:
            for col in _parse_ident_list(uq_m.group(1)):
                _add_tag(by_lower.get(col.lower()), "UQ")
            continue
        fk_m = _TABLE_FK_RE.match(constr)
        if fk_m is not None:
            src_cols = _parse_ident_list(fk_m.group(1))
            ref_tbl = _unquote_ident(fk_m.group(2))
            ref_cols = _parse_ident_list(fk_m.group(3)) if fk_m.group(3) else []
            for idx, col in enumerate(src_cols):
                ref_c = ref_cols[idx] if idx < len(ref_cols) else ""
                tag = f"FK->{ref_tbl}.{ref_c}" if ref_c else f"FK->{ref_tbl}"
                _add_tag(by_lower.get(col.lower()), tag)


def _parse_ident_list(raw: str) -> list[str]:
    return [_unquote_ident(part.split()[0]) for part in raw.split(",") if part.strip()]


def _add_tag(column: _SqlColumn | None, tag: str) -> None:
    if column is not None and tag not in column.tags:
        column.tags.append(tag)


def _format_col_entry(name: str, col_type: str, tags: list[str]) -> str:
    parts = [name]
    if col_type:
        parts.append(col_type)
    parts.extend(tags)
    return " ".join(parts)


def _format_schema_outline(
    tables: list[_SqlTable],
    views: list[str],
) -> SymbolOutline | None:
    if not tables and not views:
        return None
    compact: list[str] = []
    detailed: list[str] = []

    if tables:
        compact.append(f"tables: {', '.join(t.name for t in tables)}")
        for tbl in tables:
            detailed.append(f"{tbl.name}({', '.join(tbl.columns)})")
    if views:
        compact.append(f"views: {', '.join(views)}")
        for view in views:
            detailed.append(f"view {view}")

    return SymbolOutline(compact_lines=tuple(compact), detailed_lines=tuple(detailed))
