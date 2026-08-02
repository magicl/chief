# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Path safety gate for vault file operations.

Resolves an agent-supplied relative path against a vault's filesystem root,
rejecting anything that would escape the vault or land outside the caller's
configured roots. This is the only place path traversal defenses should live
for the vault service — callers must route all file access through
`resolve_under_roots` rather than joining paths themselves.
"""

from pathlib import Path, PurePosixPath


class PathGateError(Exception):
    """Raised when a requested path fails the vault path safety gate.

    The message is safe to surface to callers (no filesystem details beyond
    the caller-supplied relative path).
    """


def _normalize_rel_path(rel_path: str) -> PurePosixPath:
    """Parse rel_path as posix, rejecting absolute or empty paths.

    Assumes the caller wants forward-slash relative paths regardless of host
    OS, since vault content paths are always posix-style.
    """
    if not rel_path:
        raise PathGateError('Path must not be empty')
    posix_path = PurePosixPath(rel_path)
    if posix_path.is_absolute():
        raise PathGateError(f'Path must be relative: {rel_path!r}')
    return posix_path


def _matches_configured_root(parts: tuple[str, ...], roots: list[str]) -> bool:
    """Return True if parts starts with one of roots as a full path segment.

    Segment-aware: a configured root of 'Journal' matches 'Journal/x' but not
    'Journalism/x', since we compare whole path segments rather than string
    prefixes.
    """
    for root in roots:
        root_parts = PurePosixPath(root).parts
        if not root_parts:
            continue
        if parts[: len(root_parts)] == root_parts:
            return True
    return False


def resolve_under_roots(vault_root: Path, *, roots: list[str], rel_path: str) -> Path:
    """Resolve rel_path under vault_root, gated to the configured roots.

    Rejects absolute/empty paths, `..` traversal that would escape
    vault_root, and any path not under one of the caller's configured
    `roots` (segment-aware prefix match). Raises `PathGateError` on any
    violation. Does not touch the filesystem — this is a pure path check.
    """
    posix_path = _normalize_rel_path(rel_path)
    parts = posix_path.parts

    if '..' in parts:
        raise PathGateError(f'Path must not contain ".." segments: {rel_path!r}')

    if not _matches_configured_root(parts, roots):
        raise PathGateError(f'Path is outside configured roots: {rel_path!r}')

    resolved = (vault_root / posix_path).resolve()
    resolved_vault_root = vault_root.resolve()
    if resolved != resolved_vault_root and resolved_vault_root not in resolved.parents:
        raise PathGateError(f'Path escapes vault root: {rel_path!r}')

    # Return the unresolved join (not `resolved`): callers do IO against this
    # path, and continuous `ob sync` may swap symlinks/files between this gate
    # check and that IO (TOCTOU). Re-resolving here wouldn't close that window
    # anyway, so the return value intentionally matches what was gate-checked
    # rather than a resolved path that could diverge again before use.
    return vault_root / posix_path
