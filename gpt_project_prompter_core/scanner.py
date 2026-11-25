from __future__ import annotations

import hashlib
import os
from pathlib import Path

from gpt_project_prompter_core.domain import (
    CollapsedFolder,
    ProjectScanConfig,
    ProjectStructureSnapshot,
)
from gpt_project_prompter_core.file_sources import (
    FileSourceResult,
    GitFileSource,
    WalkFileSource,
)
from gpt_project_prompter_core.folder_policy import FolderPolicy, format_collapsed_folder
from gpt_project_prompter_core.interfaces import FileSourceProtocol
from gpt_project_prompter_core.renderer import render_tree_from_snapshot


class ProjectScanner:
    """Оркестрирует выбор источника файлов, политику сворачивания и сборку снимка."""

    def __init__(
        self,
        git_source: FileSourceProtocol | None = None,
        walk_source: FileSourceProtocol | None = None,
    ) -> None:
        self._git_source: FileSourceProtocol = git_source or GitFileSource()
        self._walk_source: FileSourceProtocol = walk_source or WalkFileSource()

    def scan(self, config: ProjectScanConfig) -> ProjectStructureSnapshot:
        """Сканирует проект и возвращает детерминированный ProjectStructureSnapshot."""
        root = config.root_path.resolve()
        if not root.exists() or not root.is_dir():
            return ProjectStructureSnapshot(
                structure_text="",
                structure_hash=hashlib.sha256(b"").hexdigest(),
                canonical_relative_paths=(),
                collapsed_folders=(),
            )

        policy = FolderPolicy(
            max_files_in_folder=config.max_files_in_folder,
            max_files_in_subtree=config.max_files_in_subtree,
            expand_folders=config.expand_folders,
            collapse_folders=config.collapse_folders,
        )

        source_result = self._collect_from_sources(config, policy)
        files, collapsed = _rebase_paths(source_result, root, config.base_path)
        structure_hash = _compute_structure_hash(files, collapsed)

        preliminary = ProjectStructureSnapshot(
            structure_text="",
            structure_hash=structure_hash,
            canonical_relative_paths=files,
            collapsed_folders=collapsed,
        )

        if config.gpt_format:
            structure_text = _format_plain_structure(files, collapsed)
        else:
            structure_text = render_tree_from_snapshot(preliminary, prefix=config.prefix)

        return ProjectStructureSnapshot(
            structure_text=structure_text,
            structure_hash=structure_hash,
            canonical_relative_paths=files,
            collapsed_folders=collapsed,
        )

    def _collect_from_sources(
        self,
        config: ProjectScanConfig,
        policy: FolderPolicy,
    ) -> FileSourceResult:
        if config.use_git:
            git_res = self._git_source.collect(config, policy)
            if git_res is not None:
                return git_res
        walk_res = self._walk_source.collect(config, policy)
        if walk_res is not None:
            return walk_res
        return FileSourceResult(files=(), collapsed_folders=())


def _rebase_paths(
    result: FileSourceResult,
    root: Path,
    base_path: Path | None,
) -> tuple[tuple[str, ...], tuple[CollapsedFolder, ...]]:
    if base_path is None:
        return result.files, result.collapsed_folders
    base = base_path.resolve()
    if base == root:
        return result.files, result.collapsed_folders

    rebased_files = tuple(
        sorted(os.path.relpath(root / rel, base).replace("\\", "/") for rel in result.files)
    )
    rebased_collapsed = tuple(
        sorted(
            (
                CollapsedFolder(
                    rel_path=os.path.relpath(root / cf.rel_path, base).replace("\\", "/"),
                    reason=cf.reason,
                    file_count=cf.file_count,
                    is_lower_bound=cf.is_lower_bound,
                    top_extensions=cf.top_extensions,
                )
                for cf in result.collapsed_folders
            ),
            key=lambda item: item.rel_path,
        )
    )
    return rebased_files, rebased_collapsed


def _compute_structure_hash(
    files: tuple[str, ...],
    collapsed: tuple[CollapsedFolder, ...],
) -> str:
    # Хэш зависит только от allowlist и путей свёрнутых папок, не от символов/фактов
    lines = list(files) + [f"COLLAPSED:{cf.rel_path}" for cf in collapsed]
    payload = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _format_plain_structure(
    files: tuple[str, ...],
    collapsed: tuple[CollapsedFolder, ...],
) -> str:
    rows: list[tuple[str, str]] = [(p, p) for p in files]
    rows.extend((f"{cf.rel_path}/", format_collapsed_folder(cf)) for cf in collapsed)
    rows.sort(key=lambda pair: pair[0])
    if not rows:
        return ""
    return "\n".join(line for _, line in rows) + "\n"
