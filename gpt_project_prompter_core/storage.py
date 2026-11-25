from __future__ import annotations

from collections.abc import Iterator, Sequence
import contextlib
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from gpt_project_prompter_core.domain import FileIndexRecord
from gpt_project_prompter_core.exceptions import (
    CacheStorageError,
    CorruptedCacheError,
)

_SQLITE_CHUNK_SIZE = 500


@contextlib.contextmanager
def _open_sqlite(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Открывает соединение SQLite, управляет транзакцией и гарантированно закрывает его."""
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=5.0)
    except sqlite3.Error as exc:
        raise CacheStorageError(f"Не удалось подключиться к базе кэша SQLite: {exc}") from exc

    with contextlib.closing(conn):
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        with conn:
            yield conn


def _init_all_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_cache (
            cache_key TEXT PRIMARY KEY,
            task TEXT NOT NULL,
            model TEXT NOT NULL,
            structure_hash TEXT NOT NULL,
            structure_text TEXT NOT NULL,
            selected_files_json TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_project_cache_lookup
        ON project_cache (structure_hash, task, model);
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS file_index (
            rel_path TEXT PRIMARY KEY,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            index_version INTEGER NOT NULL,
            facts_json TEXT NOT NULL,
            outline_json TEXT
        );
        """
    )


class SQLiteCacheRepository:
    """Хранилище кэша выбора файлов и таблицы file_index в SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self.initialize_schema()

    def initialize_schema(self) -> None:
        """Создаёт необходимые таблицы и индексы в базе кэша."""
        try:
            with _open_sqlite(self._db_path) as conn:
                _init_all_tables(conn)
        except sqlite3.Error as exc:
            raise CacheStorageError(f"Ошибка инициализации схемы базы кэша: {exc}") from exc

    @staticmethod
    def calculate_cache_key(task: str, model: str, structure_hash: str) -> str:
        """Вычисляет детерминированный SHA-256 ключ кэша выбора."""
        raw = f"{task.strip()}\n{model.strip()}\n{structure_hash.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_selection(
        self,
        task: str,
        model: str,
        structure_hash: str,
    ) -> tuple[str, ...] | None:
        """Возвращает сохранённый список путей/масок или None при промахе."""
        cache_key = self.calculate_cache_key(task, model, structure_hash)
        try:
            with _open_sqlite(self._db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT selected_files_json
                    FROM project_cache
                    WHERE cache_key = ?
                    LIMIT 1;
                    """,
                    (cache_key,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return _deserialize_selection(str(row[0]))
        except sqlite3.Error as exc:
            raise CacheStorageError(f"Ошибка чтения из базы кэша: {exc}") from exc

    def store_selection(
        self,
        task: str,
        model: str,
        structure_hash: str,
        structure_text: str,
        selected_files: Sequence[str],
    ) -> None:
        """Сохраняет список путей/масок выбора в кэш SQLite."""
        cache_key = self.calculate_cache_key(task, model, structure_hash)
        serialized = json.dumps(list(selected_files), ensure_ascii=False)
        now_ts = int(time.time())

        try:
            with _open_sqlite(self._db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO project_cache (
                        cache_key,
                        task,
                        model,
                        structure_hash,
                        structure_text,
                        selected_files_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        cache_key,
                        task,
                        model,
                        structure_hash,
                        structure_text,
                        serialized,
                        now_ts,
                    ),
                )
        except sqlite3.Error as exc:
            raise CacheStorageError(f"Ошибка записи в базу кэша: {exc}") from exc


class SQLiteIndexCacheRepository:
    """Пакетный кэш индекса файлов в таблице file_index общей SQLite-базы."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        try:
            with _open_sqlite(self._db_path) as conn:
                _init_all_tables(conn)
        except sqlite3.Error as exc:
            raise CacheStorageError(f"Ошибка инициализации file_index: {exc}") from exc

    def get_many(self, rel_paths: Sequence[str]) -> dict[str, FileIndexRecord]:
        """Загружает записи кэша пачкой одним соединением SQLite."""
        if not rel_paths:
            return {}
        result: dict[str, FileIndexRecord] = {}
        try:
            with _open_sqlite(self._db_path) as conn:
                cur = conn.cursor()
                for i in range(0, len(rel_paths), _SQLITE_CHUNK_SIZE):
                    chunk = rel_paths[i : i + _SQLITE_CHUNK_SIZE]
                    placeholders = ",".join("?" * len(chunk))
                    cur.execute(
                        f"SELECT rel_path, mtime_ns, size, index_version, facts_json, outline_json "
                        f"FROM file_index WHERE rel_path IN ({placeholders});",
                        tuple(chunk),
                    )
                    for row in cur.fetchall():
                        rec = FileIndexRecord(
                            rel_path=str(row[0]),
                            mtime_ns=int(row[1]),
                            size=int(row[2]),
                            index_version=int(row[3]),
                            facts_json=str(row[4]),
                            outline_json=str(row[5]) if row[5] is not None else None,
                        )
                        result[rec.rel_path] = rec
            return result
        except sqlite3.Error as exc:
            raise CacheStorageError(f"Ошибка пакетного чтения file_index: {exc}") from exc

    def put_many(self, records: Sequence[FileIndexRecord]) -> None:
        """Сохраняет обновлённые индексные записи пачкой одним соединением."""
        if not records:
            return
        rows = [
            (r.rel_path, r.mtime_ns, r.size, r.index_version, r.facts_json, r.outline_json)
            for r in records
        ]
        try:
            with _open_sqlite(self._db_path) as conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO file_index (
                        rel_path, mtime_ns, size, index_version, facts_json, outline_json
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    rows,
                )
        except sqlite3.Error as exc:
            raise CacheStorageError(f"Ошибка пакетной записи file_index: {exc}") from exc

    def prune_except(self, valid_rel_paths: Sequence[str]) -> None:
        """Удаляет из file_index пути, отсутствующие в текущем allowlist."""
        valid_set = set(valid_rel_paths)
        try:
            with _open_sqlite(self._db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT rel_path FROM file_index;")
                stale = [str(row[0]) for row in cur.fetchall() if str(row[0]) not in valid_set]
                for i in range(0, len(stale), _SQLITE_CHUNK_SIZE):
                    chunk = stale[i : i + _SQLITE_CHUNK_SIZE]
                    placeholders = ",".join("?" * len(chunk))
                    conn.execute(
                        f"DELETE FROM file_index WHERE rel_path IN ({placeholders});",
                        tuple(chunk),
                    )
        except sqlite3.Error as exc:
            raise CacheStorageError(f"Ошибка очистки file_index: {exc}") from exc


def _deserialize_selection(raw_json: str) -> tuple[str, ...]:
    try:
        parsed: object = json.loads(raw_json)
    except json.JSONDecodeError as err:
        raise CorruptedCacheError(f"Ошибка десериализации кэша JSON: {err}") from err
    if not isinstance(parsed, list):
        raise CorruptedCacheError(f"Кэш поврежден: ожидался список, получено {type(parsed)}")
    return tuple(str(item) for item in parsed)
