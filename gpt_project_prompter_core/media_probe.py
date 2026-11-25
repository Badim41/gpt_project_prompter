from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import wave

from gpt_project_prompter_core.constants import (
    FFPROBE_TIMEOUT_SECONDS,
    MAX_IMAGE_HEADER_BYTES,
    MAX_SVG_PROBE_BYTES,
)

logger = logging.getLogger(__name__)

_SVG_TAG_RE = re.compile(r"<svg\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_SVG_ATTR_RE = re.compile(r"""(\w+)\s*=\s*["']([^"']+)["']""")
_NUM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)")


@dataclass(frozen=True, slots=True)
class MediaProbeResult:
    """Размеры видео/изображения и длительность медиафайла."""

    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None


def format_size(size_bytes: int) -> str:
    """Форматирует вес файла в байтах кратно 1024."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    kb = size_bytes / 1024
    if kb < 1024:
        return f"{round(kb)} KB" if kb >= 10 else f"{kb:.1f} KB".replace(".0 KB", " KB")
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024
    return f"{gb:.1f} GB"


def format_duration(seconds: float) -> str:
    """Форматирует длительность как m:ss или h:mm:ss от одного часа."""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def probe_image_dimensions(path: Path) -> tuple[int, int] | None:
    """Читает размеры изображения из заголовка без сторонних библиотек."""
    suffix = path.suffix.lower()
    read_limit = MAX_SVG_PROBE_BYTES if suffix == ".svg" else MAX_IMAGE_HEADER_BYTES
    try:
        with path.open("rb") as fp:
            data = fp.read(read_limit)
    except OSError:
        return None
    return parse_image_dimensions_bytes(data, suffix)


def parse_image_dimensions_bytes(data: bytes, suffix: str) -> tuple[int, int] | None:
    """Извлекает (width, height) из байтов заголовка изображения."""
    ext = suffix.lower()
    if ext == ".png":
        return _parse_png(data)
    if ext == ".gif":
        return _parse_gif(data)
    if ext == ".bmp":
        return _parse_bmp(data)
    if ext in (".jpg", ".jpeg"):
        return _parse_jpeg(data)
    if ext == ".webp":
        return _parse_webp(data)
    if ext == ".ico":
        return _parse_ico(data)
    if ext == ".svg":
        return _parse_svg(data)
    return None


def _valid_dims(width: int, height: int) -> tuple[int, int] | None:
    return (width, height) if width > 0 and height > 0 else None


def _parse_png(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return _valid_dims(w, h)


def _parse_gif(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    w, h = struct.unpack("<HH", data[6:10])
    return _valid_dims(w, h)


def _parse_bmp(data: bytes) -> tuple[int, int] | None:
    if len(data) < 22 or data[:2] != b"BM":
        return None
    dib_size = struct.unpack("<I", data[14:18])[0]
    if dib_size == 12:
        w, h = struct.unpack("<HH", data[18:22])
        return _valid_dims(w, h)
    if dib_size >= 40 and len(data) >= 26:
        w, h = struct.unpack("<ii", data[18:26])
        return _valid_dims(w, abs(h))
    return None


def _parse_jpeg(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    idx = 2
    size = len(data)
    while idx + 1 < size:
        while idx < size and data[idx] != 0xFF:
            idx += 1
        while idx < size and data[idx] == 0xFF:
            idx += 1
        if idx >= size:
            return None
        marker = data[idx]
        idx += 1
        if marker in (0xD9, 0xDA):
            return None
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if idx + 2 > size:
            return None
        seg_len = struct.unpack(">H", data[idx : idx + 2])[0]
        if seg_len < 2 or idx + seg_len > size:
            return None
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if seg_len < 7:
                return None
            h, w = struct.unpack(">HH", data[idx + 3 : idx + 7])
            return _valid_dims(w, h)
        idx += seg_len
    return None


def _parse_webp(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8 ":
        if (data[20] & 1) != 0 or data[23:26] != b"\x9d\x01\x2a":
            return None
        w_raw, h_raw = struct.unpack("<HH", data[26:30])
        return _valid_dims(w_raw & 0x3FFF, h_raw & 0x3FFF)
    if chunk == b"VP8L":
        if len(data) < 25 or data[20] != 0x2F:
            return None
        bits = struct.unpack("<I", data[21:25])[0]
        return _valid_dims((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8X":
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        return _valid_dims(w, h)
    return None


def _parse_ico(data: bytes) -> tuple[int, int] | None:
    if len(data) < 22 or data[:4] != b"\x00\x00\x01\x00":
        return None
    count = struct.unpack("<H", data[4:6])[0]
    if count < 1:
        return None
    w = data[6] or 256
    h = data[7] or 256
    return _valid_dims(w, h)


def _parse_svg(data: bytes) -> tuple[int, int] | None:
    text = data[:MAX_SVG_PROBE_BYTES].decode("utf-8", errors="replace")
    match = _SVG_TAG_RE.search(text)
    if match is None:
        return None
    attrs = {k.lower(): v for k, v in _SVG_ATTR_RE.findall(match.group(1))}
    w = _parse_svg_length(attrs.get("width"))
    h = _parse_svg_length(attrs.get("height"))
    if w is not None and h is not None:
        return _valid_dims(w, h)
    viewbox = attrs.get("viewbox")
    if viewbox is None:
        return None
    parts = [p for p in re.split(r"[\s,]+", viewbox.strip()) if p]
    if len(parts) != 4:
        return None
    try:
        vb_w = int(round(float(parts[2])))
        vb_h = int(round(float(parts[3])))
    except ValueError:
        return None
    return _valid_dims(vb_w, vb_h)


def _parse_svg_length(raw: str | None) -> int | None:
    if raw is None or "%" in raw:
        return None
    match = _NUM_RE.match(raw)
    if match is None:
        return None
    try:
        return int(round(float(match.group(1))))
    except ValueError:
        return None


def probe_wav_duration(path: Path) -> float | None:
    """Вычисляет длительность WAV через stdlib wave."""
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            frames = wf.getnframes()
            return (frames / rate) if rate > 0 else None
    except (wave.Error, OSError, EOFError):
        return None


def probe_flac_duration(path: Path) -> float | None:
    """Извлекает длительность FLAC из блока STREAMINFO."""
    try:
        with path.open("rb") as fp:
            if fp.read(4) != b"fLaC":
                return None
            while True:
                header = fp.read(4)
                if len(header) < 4:
                    return None
                is_last = bool(header[0] & 0x80)
                block_type = header[0] & 0x7F
                length = int.from_bytes(header[1:4], "big")
                if block_type == 0:
                    payload = fp.read(length)
                    return _duration_from_flac_streaminfo(payload)
                fp.seek(length, 1)
                if is_last:
                    return None
    except OSError:
        return None


def _duration_from_flac_streaminfo(payload: bytes) -> float | None:
    if len(payload) < 18:
        return None
    packed = struct.unpack(">Q", payload[10:18])[0]
    sample_rate = (packed >> 44) & 0xFFFFF
    total_samples = packed & 0xFFFFFFFFF
    if sample_rate <= 0 or total_samples <= 0:
        return None
    return total_samples / sample_rate


def probe_mp4_duration(path: Path) -> float | None:
    """Извлекает длительность MP4/M4A/MOV из атома moov/mvhd (версии 0 и 1)."""
    try:
        file_size = path.stat().st_size
        with path.open("rb") as fp:
            return _scan_mp4_boxes(fp, 0, file_size, in_moov=False)
    except OSError:
        return None


def _scan_mp4_boxes(
    fp: object,
    start: int,
    end: int,
    *,
    in_moov: bool,
) -> float | None:
    pos = start
    while pos + 8 <= end:
        fp.seek(pos)  # type: ignore[attr-defined]
        header: bytes = fp.read(8)  # type: ignore[attr-defined]
        if len(header) < 8:
            return None
        box_size, box_type = struct.unpack(">I4s", header)
        header_len = 8
        if box_size == 1:
            ext_raw: bytes = fp.read(8)  # type: ignore[attr-defined]
            if len(ext_raw) < 8:
                return None
            box_size = struct.unpack(">Q", ext_raw)[0]
            header_len = 16
        elif box_size == 0:
            box_size = end - pos
        if box_size < header_len or pos + box_size > end:
            return None
        payload_start = pos + header_len
        payload_end = pos + box_size
        if not in_moov and box_type == b"moov":
            found = _scan_mp4_boxes(fp, payload_start, payload_end, in_moov=True)
            if found is not None:
                return found
        elif in_moov and box_type == b"mvhd":
            fp.seek(payload_start)  # type: ignore[attr-defined]
            mvhd_data: bytes = fp.read(min(64, payload_end - payload_start))  # type: ignore[attr-defined]
            return _parse_mvhd_duration(mvhd_data)
        pos = payload_end
    return None


def _parse_mvhd_duration(payload: bytes) -> float | None:
    if not payload:
        return None
    version = payload[0]
    if version == 0 and len(payload) >= 20:
        timescale, duration = struct.unpack(">II", payload[12:20])
    elif version == 1 and len(payload) >= 32:
        timescale, duration = struct.unpack(">IQ", payload[20:32])
    else:
        return None
    return (duration / timescale) if timescale > 0 else None


class SubprocessFfprobeRunner:
    """Вызывает ffprobe без shell с таймаутом 5 с для анализа аудио и видео."""

    def probe(self, path: Path) -> MediaProbeResult | None:
        """Возвращает MediaProbeResult от ffprobe или None, если утилита недоступна."""
        ffprobe_bin = shutil.which("ffprobe")
        if ffprobe_bin is None:
            return None
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            str(path.resolve()),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=FFPROBE_TIMEOUT_SECONDS,
                check=False,
                creationflags=creationflags,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("ffprobe не выполнен для %s: %s", path, exc)
            return None
        if proc.returncode != 0:
            return None
        return _parse_ffprobe_json(proc.stdout)


def _parse_ffprobe_json(raw: bytes) -> MediaProbeResult | None:
    try:
        parsed: object = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    duration: float | None = None
    fmt = parsed.get("format")
    if isinstance(fmt, dict) and fmt.get("duration") is not None:
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = None

    width: int | None = None
    height: int | None = None
    streams = parsed.get("streams")
    if isinstance(streams, list):
        for stream in streams:
            if not isinstance(stream, dict) or stream.get("codec_type") != "video":
                continue
            w_raw = stream.get("width")
            h_raw = stream.get("height")
            if isinstance(w_raw, int) and isinstance(h_raw, int) and w_raw > 0 and h_raw > 0:
                width, height = w_raw, h_raw
                break

    if duration is None and width is None:
        return None
    return MediaProbeResult(width=width, height=height, duration_seconds=duration)


class MediaProbe:
    """Объединяет ffprobe и stdlib-парсеры заголовков медиафайлов."""

    def __init__(self, ffprobe_runner: SubprocessFfprobeRunner | None = None) -> None:
        self._ffprobe = ffprobe_runner or SubprocessFfprobeRunner()

    def probe_image(self, path: Path) -> MediaProbeResult:
        """Возвращает размеры изображения или пустой результат при битом заголовке."""
        dims = probe_image_dimensions(path)
        if dims is None:
            return MediaProbeResult()
        return MediaProbeResult(width=dims[0], height=dims[1])

    def probe_audio_video(self, path: Path) -> MediaProbeResult:
        """Сначала пробует ffprobe, при отсутствии использует stdlib-разбор."""
        ff_res = self._ffprobe.probe(path)
        if ff_res is not None:
            return ff_res
        ext = path.suffix.lower()
        duration: float | None = None
        if ext == ".wav":
            duration = probe_wav_duration(path)
        elif ext == ".flac":
            duration = probe_flac_duration(path)
        elif ext in (".mp4", ".m4a", ".mov"):
            duration = probe_mp4_duration(path)
        return MediaProbeResult(duration_seconds=duration)
