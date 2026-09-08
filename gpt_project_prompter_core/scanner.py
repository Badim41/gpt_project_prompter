import hashlib
import os
from pathlib import Path

from gpt_project_prompter_core.domain import (
    ProjectScanConfig,
    ProjectStructureSnapshot,
)
from gpt_project_prompter_core.exceptions import ProjectScanIOError


class ProjectScanner:
    def _render_tree(
        self,
        dir_path: Path,
        config: ProjectScanConfig,
        prefix: str = "",
        visited_inodes: set[tuple[int, int]] | None = None,
    ) -> str:
        if visited_inodes is None:
            visited_inodes = set()

        try:
            stat_val = dir_path.stat()
            inode_key = (stat_val.st_dev, stat_val.st_ino)
            if inode_key in visited_inodes:
                return ""
            visited_inodes.add(inode_key)

            with os.scandir(dir_path) as iterator:
                entries = [e for e in iterator if not e.name.startswith(".")]
                entries.sort(key=lambda e: e.name)
        except (OSError, PermissionError) as exc:
            raise ProjectScanIOError(f"Ошибка доступа к директории {dir_path}: {exc}") from exc

        filtered: list[os.DirEntry] = []
        for entry in entries:
            if entry.is_dir(follow_symlinks=False) and entry.name in config.ignore_folders:
                continue
            if entry.is_file(follow_symlinks=False) and entry.name in config.ignore_files:
                continue
            filtered.append(entry)

        result_parts: list[str] = []
        total = len(filtered)
        for i, entry in enumerate(filtered):
            is_last = (i == total - 1)
            connector = "└── " if is_last else "├── "
            result_parts.append(f"{prefix}{connector}{entry.name}\n")

            if entry.is_dir(follow_symlinks=False):
                extension = "    " if is_last else "│   "
                subtree = self._render_tree(
                    dir_path=Path(entry.path),
                    config=config,
                    prefix=prefix + extension,
                    visited_inodes=visited_inodes,
                )
                if subtree:
                    result_parts.append(subtree)

        return "".join(result_parts)

    def scan(self, config: ProjectScanConfig) -> ProjectStructureSnapshot:
        root = config.root_path.resolve()
        base = config.base_path.resolve() if config.base_path is not None else root
        if not root.exists() or not root.is_dir():
            return ProjectStructureSnapshot(
                structure_text="",
                structure_hash=hashlib.sha256(b"").hexdigest(),
                canonical_relative_paths=(),
            )

        canonical_paths: list[str] = []
        visited_inodes: set[tuple[int, int]] = set()

        def _walk(current_dir: Path) -> None:
            try:
                stat_val = current_dir.stat()
                inode_key = (stat_val.st_dev, stat_val.st_ino)
                if inode_key in visited_inodes:
                    return
                visited_inodes.add(inode_key)

                with os.scandir(current_dir) as iterator:
                    entries = sorted(list(iterator), key=lambda e: e.name)
            except (OSError, PermissionError) as exc:
                raise ProjectScanIOError(f"Ошибка доступа к директории {current_dir}: {exc}") from exc

            for entry in entries:
                if entry.name.startswith("."):
                    continue

                if entry.is_dir(follow_symlinks=False):
                    if entry.name in config.ignore_folders:
                        continue
                    _walk(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    if entry.name in config.ignore_files:
                        continue
                    rel_path = os.path.relpath(entry.path, base).replace("\\", "/")
                    canonical_paths.append(rel_path)

        _walk(root)
        canonical_paths.sort()

        if config.gpt_format:
            structure_text = "\n".join(canonical_paths) + ("\n" if canonical_paths else "")
        else:
            structure_text = self._render_tree(
                dir_path=root,
                config=config,
                prefix=config.prefix,
            )

        structure_hash = hashlib.sha256(
            ("\n".join(canonical_paths) + ("\n" if canonical_paths else "")).encode("utf-8")
        ).hexdigest()

        return ProjectStructureSnapshot(
            structure_text=structure_text,
            structure_hash=structure_hash,
            canonical_relative_paths=tuple(canonical_paths),
        )
