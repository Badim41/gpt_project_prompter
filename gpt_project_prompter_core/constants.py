from __future__ import annotations

DEFAULT_MODEL: str = "gemini-4-hydrogen"
INDEX_VERSION: int = 1

DEFAULT_MAX_FILES_IN_FOLDER: int = 60
DEFAULT_MAX_FILES_IN_SUBTREE: int = 5000
DEFAULT_DETAIL_LEVEL: int = 3
DEFAULT_MAX_STRUCTURE_CHARS: int = 750_000
DEFAULT_MAX_PROMPT_CHARS: int = 500_000

PROMPT_RESERVE_CHARS: int = 2_000
MAX_FILES_PER_PATTERN: int = 100
SOURCE_CODE_RATIO_THRESHOLD: float = 0.70
SOURCE_CODE_LIMIT_MULTIPLIER: int = 4

MAX_TEXT_READ_BYTES: int = 2 * 1024 * 1024
MAX_PYTHON_AST_BYTES: int = 1 * 1024 * 1024
MAX_JS_TS_BYTES: int = 1 * 1024 * 1024
MAX_JSON_BYTES: int = 64 * 1024
MAX_SQL_BYTES: int = 256 * 1024
MAX_SVG_PROBE_BYTES: int = 2 * 1024
MAX_IMAGE_HEADER_BYTES: int = 256 * 1024

MAX_SIGNATURE_CHARS: int = 160
MAX_DOCSTRING_CHARS: int = 80
MAX_JSON_SIGNATURE_CHARS: int = 300
MAX_JSON_KEYS: int = 12
MAX_JSON_DEPTH: int = 3
MAX_CLASS_ATTRS: int = 12
MAX_LOCAL_IMPORTS: int = 8
MAX_SQL_TABLES: int = 40
MAX_SQL_COLUMNS: int = 30

GIT_TIMEOUT_SECONDS: int = 10
FFPROBE_TIMEOUT_SECONDS: int = 5

SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cc",
        ".cs",
        ".php",
        ".rb",
        ".swift",
        ".sql",
        ".sh",
        ".ps1",
    }
)

PYTHON_EXTENSIONS: frozenset[str] = frozenset({".py", ".pyi"})
JS_TS_EXTENSIONS: frozenset[str] = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"})
SQLITE_EXTENSIONS: frozenset[str] = frozenset({".db", ".sqlite", ".sqlite3", ".db3"})
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".gif", ".bmp", ".jpg", ".jpeg", ".webp", ".ico", ".svg"}
)
AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".wav", ".flac", ".mp3", ".ogg", ".opus", ".aac", ".m4a"}
)
VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi"})

VENV_FILE_MARKERS: frozenset[str] = frozenset({"pyvenv.cfg"})
VENV_DIR_MARKERS: frozenset[str] = frozenset({"conda-meta"})
DEPENDENCY_DIR_NAMES: frozenset[str] = frozenset(
    {"node_modules", "site-packages", "__pycache__"}
)
