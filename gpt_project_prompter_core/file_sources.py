from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys

from gpt_project_prompter_core.constants import GIT_TIMEOUT_SECONDS
from gpt_project_prompter_core.domain import (
    CollapsedFolder,
    CollapseReason,
    ProjectScanConfig,
)
from gpt_project_prompter_core.exceptions import ProjectScanIOError
from gpt_project_prompter_core.folder_policy import (
    FolderPolicy,
    file_extension_label,
    top_extensions_from_counts,
    top_extensions_from_names,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FileSourceResult:
    """Результат сбора файлов из источника до построения снимка."""

    files: tuple[str, ...]
    collapsed_folders: tuple[CollapsedFolder, ...]


class SubprocessGitRunner:
    """Запускает git ls-files через subprocess без shell."""

    def list_files(self, root_path: Path) -> Sequence[str] | None:
        """Возвращает относительные пути git ls-files или None при любой ошибке."""
        git_bin = shutil.which("git")
        if git_bin is None:
            return None
        cmd = [git_bin, "-C", str(root_path), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
                creationflags=creationflags,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("GitFileSource откат на обход диска: %s", exc)
            return None
        if proc.returncode != 0:
            return None
        decoded = proc.stdout.decode("utf-8", errors="replace")
        return [item for item in decoded.split("\x00") if item]


class WalkFileSource:
    """Обходит дерево через os.scandir с ранним сворачиванием папок."""

    def collect(
        self,
        config: ProjectScanConfig,
        policy: FolderPolicy,
    ) -> FileSourceResult | None:
        """Возвращает отсортированный allowlist и список свёрнутых папок."""
        root = config.root_path.resolve()
        if not root.exists() or not root.is_dir():
            return FileSourceResult(files=(), collapsed_folders=())
        visited: set[tuple[int, int]] = set()
        files, collapsed = self._walk_dir(root, "", config, policy, visited)
        files.sort()
        collapsed.sort(key=lambda item: item.rel_path)
        return FileSourceResult(files=tuple(files), collapsed_folders=tuple(collapsed))

    def _walk_dir(
        self,
        abs_dir: Path,
        rel_dir: str,
        config: ProjectScanConfig,
        policy: FolderPolicy,
        visited: set[tuple[int, int]],
    ) -> tuple[list[str], list[CollapsedFolder]]:
        if rel_dir and policy.is_configured_collapse(rel_dir):
            return [], [self._count_configured_collapse(abs_dir, rel_dir, config, policy)]

        junk_reason = policy.detect_junk_marker(rel_dir, abs_dir)
        if junk_reason is not None:
            return [], [CollapsedFolder(rel_path=rel_dir, reason=junk_reason)]

        entries = self._scan_dir_safe(abs_dir, visited)
        if entries is None:
            return [], []

        direct_files, subdirs = self._partition_entries(entries, config)
        direct_names = [entry.name for entry in direct_files]

        if policy.should_collapse_direct(rel_dir, direct_names):
            return [], [
                CollapsedFolder(
                    rel_path=rel_dir,
                    reason=CollapseReason.TOO_MANY_FILES,
                    file_count=len(direct_names),
                    is_lower_bound=False,
                    top_extensions=top_extensions_from_names(direct_names),
                )
            ]

        return self._collect_subtree(rel_dir, direct_files, subdirs, config, policy, visited)

    def _collect_subtree(
        self,
        rel_dir: str,
        direct_files: list[os.DirEntry[str]],
        subdirs: list[os.DirEntry[str]],
        config: ProjectScanConfig,
        policy: FolderPolicy,
        visited: set[tuple[int, int]],
    ) -> tuple[list[str], list[CollapsedFolder]]:
        collected_files: list[str] = []
        collected_collapsed: list[CollapsedFolder] = []
        ext_counts: Counter[str] = Counter()

        for entry in direct_files:
            rel_path = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            collected_files.append(rel_path)
            ext_counts[file_extension_label(entry.name)] += 1

        for subdir in subdirs:
            child_rel = f"{rel_dir}/{subdir.name}" if rel_dir else subdir.name
            child_files, child_collapsed = self._walk_dir(
                Path(subdir.path), child_rel, config, policy, visited
            )
            collected_files.extend(child_files)
            collected_collapsed.extend(child_collapsed)
            for path_item in child_files:
                ext_counts[file_extension_label(path_item)] += 1

            if policy.should_collapse_subtree(rel_dir, len(collected_files)):
                return [], [
                    CollapsedFolder(
                        rel_path=rel_dir,
                        reason=CollapseReason.TOO_MANY_SUBTREE_FILES,
                        file_count=len(collected_files),
                        is_lower_bound=True,
                        top_extensions=top_extensions_from_counts(ext_counts),
                    )
                ]

        return collected_files, collected_collapsed

    def _count_configured_collapse(
        self,
        abs_dir: Path,
        rel_dir: str,
        config: ProjectScanConfig,
        policy: FolderPolicy,
    ) -> CollapsedFolder:
        limit = policy.max_files_in_subtree
        count = 0
        ext_counts: Counter[str] = Counter()
        stack: list[Path] = [abs_dir]
        visited: set[tuple[int, int]] = set()
        lower_bound = False

        while stack:
            current = stack.pop()
            entries = self._scan_dir_safe(current, visited)
            if entries is None:
                continue
            files, subdirs = self._partition_entries(entries, config)
            for f_entry in files:
                count += 1
                ext_counts[file_extension_label(f_entry.name)] += 1
                if count > limit:
                    lower_bound = True
                    break
            if lower_bound:
                break
            stack.extend(Path(sub.path) for sub in subdirs)

        return CollapsedFolder(
            rel_path=rel_dir,
            reason=CollapseReason.CONFIGURED,
            file_count=count,
            is_lower_bound=lower_bound,
            top_extensions=top_extensions_from_counts(ext_counts),
        )

    @staticmethod
    def _scan_dir_safe(
        abs_dir: Path,
        visited: set[tuple[int, int]],
    ) -> list[os.DirEntry[str]] | None:
        try:
            stat_val = abs_dir.stat()
            inode_key = (stat_val.st_dev, stat_val.st_ino)
            if inode_key in visited:
                return None
            visited.add(inode_key)
            with os.scandir(abs_dir) as iterator:
                return sorted(iterator, key=lambda e: e.name)
        except (OSError, PermissionError) as exc:
            raise ProjectScanIOError(f"Ошибка доступа к директории {abs_dir}: {exc}") from exc

    @staticmethod
    def _partition_entries(
        entries: Sequence[os.DirEntry[str]],
        config: ProjectScanConfig,
    ) -> tuple[list[os.DirEntry[str]], list[os.DirEntry[str]]]:
        files: list[os.DirEntry[str]] = []
        subdirs: list[os.DirEntry[str]] = []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in config.ignore_folders:
                    subdirs.append(entry)
            elif entry.is_file(follow_symlinks=False):
                if entry.name not in config.ignore_files:
                    files.append(entry)
        return files, subdirs


class GitFileSource:
    """Источник файлов через git ls-files с общей FolderPolicy и тихим откатом."""

    def __init__(self, git_runner: SubprocessGitRunner | None = None) -> None:
        self._git_runner = git_runner or SubprocessGitRunner()

    def collect(
        self,
        config: ProjectScanConfig,
        policy: FolderPolicy,
    ) -> FileSourceResult | None:
        """Возвращает результат из git или None для отката на WalkFileSource."""
        if not config.use_git:
            return None
        root = config.root_path.resolve()
        raw_paths = self._git_runner.list_files(root)
        if raw_paths is None:
            return None

        filtered = self._filter_paths(root, raw_paths, config)
        dir_files, dir_children = self._build_tree_maps(filtered)
        files, collapsed = self._apply_policy("", root, dir_files, dir_children, policy)
        files.sort()
        collapsed.sort(key=lambda item: item.rel_path)
        return FileSourceResult(files=tuple(files), collapsed_folders=tuple(collapsed))

    @staticmethod
    def _filter_paths(
        root: Path,
        raw_paths: Sequence[str],
        config: ProjectScanConfig,
    ) -> list[str]:
        kept: list[str] = []
        for raw in raw_paths:
            norm = raw.replace("\\", "/").strip("/")
            if not norm:
                continue
            parts = norm.split("/")
            if any(part.startswith(".") for part in parts):
                continue
            if any(part in config.ignore_folders for part in parts[:-1]):
                continue
            if parts[-1] in config.ignore_files:
                continue
            target = root / norm
            if _is_regular_non_symlink(root, parts, target):
                kept.append(norm)
        kept.sort()
        return kept

    @staticmethod
    def _build_tree_maps(
        paths: Sequence[str],
    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        dir_files: dict[str, list[str]] = {"": []}
        dir_children_sets: dict[str, set[str]] = {"": set()}
        for rel in paths:
            parts = rel.split("/")
            parent = "/".join(parts[:-1])
            dir_files.setdefault(parent, []).append(parts[-1])
            dir_children_sets.setdefault(parent, set())
            cur = ""
            for seg in parts[:-1]:
                nxt = f"{cur}/{seg}" if cur else seg
                dir_children_sets.setdefault(cur, set()).add(nxt)
                dir_children_sets.setdefault(nxt, set())
                dir_files.setdefault(nxt, [])
                cur = nxt
        dir_children = {k: sorted(v) for k, v in dir_children_sets.items()}
        return dir_files, dir_children

    def _apply_policy(
        self,
        rel_dir: str,
        root: Path,
        dir_files: dict[str, list[str]],
        dir_children: dict[str, list[str]],
        policy: FolderPolicy,
    ) -> tuple[list[str], list[CollapsedFolder]]:
        abs_dir = root / rel_dir if rel_dir else root
        if rel_dir and policy.is_configured_collapse(rel_dir):
            all_names = self._gather_all_names(rel_dir, dir_files, dir_children)
            return [], [
                CollapsedFolder(
                    rel_path=rel_dir,
                    reason=CollapseReason.CONFIGURED,
                    file_count=len(all_names),
                    is_lower_bound=False,
                    top_extensions=top_extensions_from_names(all_names),
                )
            ]

        junk_reason = policy.detect_junk_marker(rel_dir, abs_dir)
        if junk_reason is not None:
            return [], [CollapsedFolder(rel_path=rel_dir, reason=junk_reason)]

        direct_names = dir_files.get(rel_dir, [])
        if policy.should_collapse_direct(rel_dir, direct_names):
            return [], [
                CollapsedFolder(
                    rel_path=rel_dir,
                    reason=CollapseReason.TOO_MANY_FILES,
                    file_count=len(direct_names),
                    is_lower_bound=False,
                    top_extensions=top_extensions_from_names(direct_names),
                )
            ]

        collected_files = [
            f"{rel_dir}/{name}" if rel_dir else name for name in direct_names
        ]
        collected_collapsed: list[CollapsedFolder] = []
        for child_rel in dir_children.get(rel_dir, []):
            c_files, c_collapsed = self._apply_policy(
                child_rel, root, dir_files, dir_children, policy
            )
            collected_files.extend(c_files)
            collected_collapsed.extend(c_collapsed)
            if policy.should_collapse_subtree(rel_dir, len(collected_files)):
                return [], [
                    CollapsedFolder(
                        rel_path=rel_dir,
                        reason=CollapseReason.TOO_MANY_SUBTREE_FILES,
                        file_count=len(collected_files),
                        is_lower_bound=True,
                        top_extensions=top_extensions_from_names(collected_files),
                    )
                ]

        return collected_files, collected_collapsed

    def _gather_all_names(
        self,
        rel_dir: str,
        dir_files: dict[str, list[str]],
        dir_children: dict[str, list[str]],
    ) -> list[str]:
        names = list(dir_files.get(rel_dir, []))
        for child in dir_children.get(rel_dir, []):
            names.extend(self._gather_all_names(child, dir_files, dir_children))
        return names


def _is_regular_non_symlink(root: Path, parts: Sequence[str], target: Path) -> bool:
    try:
        current = root
        for part in parts:
            current = current / part
            if current.is_symlink():
                return False
        return target.is_file()
    except OSError:
        return False
