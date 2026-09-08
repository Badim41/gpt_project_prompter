import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Sequence

from gpt_project_prompter_core.exceptions import (
    CacheStorageError,
    CorruptedCacheError,
)


class SQLiteCacheRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self.initialize_schema()

    def _get_connection(self) -> sqlite3.Connection:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            return conn
        except sqlite3.Error as exc:
            raise CacheStorageError(f"Не удалось подключиться к базе кэша SQLite: {exc}") from exc

    def initialize_schema(self) -> None:
        try:
            with self._get_connection() as conn:
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
        except sqlite3.Error as exc:
            raise CacheStorageError(f"Ошибка инициализации схемы базы кэша: {exc}") from exc

    @staticmethod
    def calculate_cache_key(task: str, model: str, structure_hash: str) -> str:
        raw = f"{task.strip()}\n{model.strip()}\n{structure_hash.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_selection(
        self,
        task: str,
        model: str,
        structure_hash: str,
    ) -> tuple[str, ...] | None:
        cache_key = self.calculate_cache_key(task, model, structure_hash)
        try:
            with self._get_connection() as conn:
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

                raw_json: str = row[0]
                try:
                    parsed = json.loads(raw_json)
                    if not isinstance(parsed, list):
                        raise CorruptedCacheError(f"Кэш поврежден: ожидался список, получено {type(parsed)}")
                    return tuple(str(item) for item in parsed)
                except json.JSONDecodeError as err:
                    raise CorruptedCacheError(f"Ошибка десериализации кэша JSON: {err}") from err
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
        cache_key = self.calculate_cache_key(task, model, structure_hash)
        serialized = json.dumps(list(selected_files), ensure_ascii=False)
        now_ts = int(time.time())

        try:
            with self._get_connection() as conn:
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