from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class FileKind(str, Enum):
    """Классификация типа файла для формирования фактов."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    SQLITE = "sqlite"
    BINARY = "binary"


@dataclass(frozen=True, slots=True)
class FileFacts:
    """Компактные метаданные файла для уровня детализации 1 и выше."""

    size_bytes: int
    kind: FileKind
    line_count: int | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None

    def to_dict(self) -> dict[str, object]:
        """Сериализует факты в JSON-совместимый словарь."""
        return {
            "size_bytes": self.size_bytes,
            "kind": self.kind.value,
            "line_count": self.line_count,
            "width": self.width,
            "height": self.height,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> FileFacts:
        """Восстанавливает факты из словаря кэша."""
        size_val = data.get("size_bytes")
        kind_val = data.get("kind")
        if not isinstance(size_val, int) or not isinstance(kind_val, str):
            raise ValueError("Некорректный формат FileFacts в кэше.")
        line_val = data.get("line_count")
        width_val = data.get("width")
        height_val = data.get("height")
        dur_val = data.get("duration_seconds")
        return cls(
            size_bytes=size_val,
            kind=FileKind(kind_val),
            line_count=line_val if isinstance(line_val, int) else None,
            width=width_val if isinstance(width_val, int) else None,
            height=height_val if isinstance(height_val, int) else None,
            duration_seconds=float(dur_val) if isinstance(dur_val, (int, float)) else None,
        )


@dataclass(frozen=True, slots=True)
class SymbolOutline:
    """Контур символов файла для уровней детализации 2 и 3."""

    compact_lines: tuple[str, ...]
    detailed_lines: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Сериализует контур в JSON-совместимый словарь."""
        return {
            "compact_lines": list(self.compact_lines),
            "detailed_lines": list(self.detailed_lines),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SymbolOutline:
        """Восстанавливает контур из словаря кэша."""
        compact = data.get("compact_lines")
        detailed = data.get("detailed_lines")
        if not isinstance(compact, list) or not isinstance(detailed, list):
            raise ValueError("Некорректный формат SymbolOutline в кэше.")
        return cls(
            compact_lines=tuple(str(item) for item in compact),
            detailed_lines=tuple(str(item) for item in detailed),
        )


@dataclass(frozen=True, slots=True)
class FileIndexEntry:
    """Объединённая индексная запись одного файла (факты + контур)."""

    rel_path: str
    facts: FileFacts
    outline: SymbolOutline | None = None


class SymbolExtractor(Protocol):
    """Интерфейс чистого экстрактора контура по байтам и имени файла."""

    def supports(self, rel_path: str) -> bool:
        ...

    def extract(
        self,
        data: bytes,
        rel_path: str,
        local_modules: frozenset[str],
    ) -> SymbolOutline | None:
        ...
