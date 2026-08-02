# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Path safety gate and O_NOFOLLOW descriptor-relative vault IO.

Logical validation rejects absolute paths, ``..``, and outside-root prefixes.
All real filesystem access walks components with ``openat(..., O_NOFOLLOW)`` so
symlinks at any level (including races after validation) cannot escape the
vault root.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath


class PathGateError(Exception):
    """Raised when a requested path fails the vault path safety gate.

    The message is safe to surface to callers (no filesystem details beyond
    the caller-supplied relative path).
    """


def _normalize_rel_path(rel_path: str) -> PurePosixPath:
    """Parse rel_path as posix, rejecting absolute, empty, or control-bearing paths."""
    if not rel_path:
        raise PathGateError('Path must not be empty')
    if '\x00' in rel_path or any(ord(ch) < 32 for ch in rel_path):
        raise PathGateError(f'Path contains forbidden characters: {rel_path!r}')
    posix_path = PurePosixPath(rel_path)
    if posix_path.is_absolute():
        raise PathGateError(f'Path must be relative: {rel_path!r}')
    return posix_path


def _matches_configured_root(parts: tuple[str, ...], roots: list[str]) -> bool:
    """Return True if parts starts with one of roots as a full path segment."""
    for root in roots:
        root_parts = PurePosixPath(root).parts
        if not root_parts:
            continue
        if parts[: len(root_parts)] == root_parts:
            return True
    return False


def validate_rel_components(rel_path: str, *, roots: list[str]) -> tuple[str, ...]:
    """Return path components after logical gate checks (no filesystem IO)."""
    posix_path = _normalize_rel_path(rel_path)
    parts = posix_path.parts
    if not parts:
        raise PathGateError('Path must not be empty')
    if any(part in ('', '.', '..') for part in parts):
        raise PathGateError(f'Path must not contain "." or ".." segments: {rel_path!r}')
    if not _matches_configured_root(parts, roots):
        raise PathGateError(f'Path is outside configured roots: {rel_path!r}')
    return parts


def resolve_under_roots(vault_root: Path, *, roots: list[str], rel_path: str) -> Path:
    """Logical join used by tests and callers that only need a Path object.

    Still rejects ``..`` / outside roots. Does not open the filesystem; IO must
    use ``open_file_under_roots`` / ``list_dir_under_roots``.
    """
    parts = validate_rel_components(rel_path, roots=roots)
    return vault_root.joinpath(*parts)


def _open_vault_root(vault_root: Path) -> int:
    """Open the vault root directory without following a final symlink."""
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_CLOEXEC', 0)
    nofollow = getattr(os, 'O_NOFOLLOW', 0)
    try:
        return os.open(vault_root, flags | nofollow)
    except OSError as exc:
        raise PathGateError(f'Vault root is not a safe directory: {vault_root}') from exc


def _ensure_dir_nofollow(dir_fd: int, name: str) -> int:
    """Open or create a subdirectory of dir_fd without following symlinks."""
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o755, dir_fd=dir_fd)
        except FileExistsError:
            # Lost the create race; retry open (still O_NOFOLLOW).
            pass
        try:
            return os.open(name, flags, dir_fd=dir_fd)
        except OSError as exc:
            raise PathGateError(f'Refused directory component {name!r} (symlink or unsafe type)') from exc
    except OSError as exc:
        raise PathGateError(f'Refused directory component {name!r} (symlink or unsafe type)') from exc


def _open_leaf(dir_fd: int, name: str, *, flags: int) -> int:
    """Open a leaf name under dir_fd with O_NOFOLLOW; require a regular file after open."""
    open_flags = flags | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(name, open_flags, 0o644, dir_fd=dir_fd)
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise
        raise PathGateError(f'Refused file component {name!r} (symlink or unsafe type)') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise PathGateError(f'Refused non-regular file at {name!r}')
        return fd
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def open_file_under_roots(
    vault_root: Path,
    *,
    roots: list[str],
    rel_path: str,
    flags: int,
    create_parents: bool = False,
) -> int:
    """Open ``rel_path`` under ``vault_root`` via O_NOFOLLOW descriptor walks.

    Returns an open file descriptor. Caller must close it. Raises PathGateError
    for gate violations / symlink escapes, FileNotFoundError when missing and
    not creating.
    """
    parts = validate_rel_components(rel_path, roots=roots)
    root_fd = _open_vault_root(vault_root)
    dir_fd = root_fd
    owned_fds = [root_fd]
    try:
        for component in parts[:-1]:
            if create_parents:
                next_fd = _ensure_dir_nofollow(dir_fd, component)
            else:
                flags_dir = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
                try:
                    next_fd = os.open(component, flags_dir, dir_fd=dir_fd)
                except FileNotFoundError:
                    raise
                except OSError as exc:
                    raise PathGateError(f'Refused directory component {component!r} (symlink or unsafe type)') from exc
            owned_fds.append(next_fd)
            dir_fd = next_fd
        return _open_leaf(dir_fd, parts[-1], flags=flags)
    finally:
        for fd in reversed(owned_fds):
            try:
                os.close(fd)
            except OSError:
                pass


def list_dir_under_roots(vault_root: Path, *, roots: list[str], rel_path: str) -> list[str]:
    """List a directory under the vault with O_NOFOLLOW; return sorted names."""
    parts = validate_rel_components(rel_path, roots=roots)
    root_fd = _open_vault_root(vault_root)
    dir_fd = root_fd
    owned_fds = [root_fd]
    try:
        for component in parts:
            flags_dir = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
            try:
                next_fd = os.open(component, flags_dir, dir_fd=dir_fd)
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise PathGateError(f'Refused directory component {component!r} (symlink or unsafe type)') from exc
            owned_fds.append(next_fd)
            dir_fd = next_fd
        return sorted(entry.name for entry in os.scandir(dir_fd))
    finally:
        for fd in reversed(owned_fds):
            try:
                os.close(fd)
            except OSError:
                pass
