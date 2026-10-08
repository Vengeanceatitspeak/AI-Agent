"""Filesystem MCP Server for JARVIS.

Tools:
    list_dir: List directory contents.
    read_file: Read file contents.
    search_files: Search for files by name pattern.
    write_file: Write content to a file.
    move_path: Move/rename a file or directory.
    delete_path: Delete a file or directory.

All operations are sandboxed to the configured FS_ROOT.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("FileSystem")

# Sandbox root — configured via FS_ROOT environment variable
_FS_ROOT: Path | None = None
_MAX_READ_SIZE = 1_000_000  # 1MB max read
_MAX_LIST_ENTRIES = 500


def _get_root() -> Path:
    """Get the filesystem root, lazily initialized."""
    global _FS_ROOT
    if _FS_ROOT is None:
        root = os.environ.get("FS_ROOT", "")
        if not root:
            raise ValueError("FS_ROOT environment variable not set")
        _FS_ROOT = Path(root).expanduser().resolve()
        if not _FS_ROOT.exists():
            _FS_ROOT.mkdir(parents=True, exist_ok=True)
    return _FS_ROOT


def _resolve_safe(path: str) -> Path:
    """Resolve a path safely within the sandbox.

    Prevents path traversal and symlink escapes.

    Raises:
        ValueError: If the path escapes the sandbox.
    """
    root = _get_root()
    # Handle relative and absolute paths
    if os.path.isabs(path):
        resolved = Path(path).resolve()
    else:
        resolved = (root / path).resolve()

    # Check it's within the root (after resolving symlinks)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"Path '{path}' resolves to '{resolved}' which is outside "
            f"the allowed root '{root}'. Access denied."
        )

    return resolved


@mcp.tool()
async def list_dir(path: str = ".") -> str:
    """List contents of a directory.

    Args:
        path: Directory path relative to workspace root. Defaults to root.

    Returns:
        Formatted directory listing with type, size, and name.
    """
    resolved = _resolve_safe(path)

    if not resolved.exists():
        return f"Error: Directory does not exist: {path}"
    if not resolved.is_dir():
        return f"Error: Not a directory: {path}"

    entries: list[str] = []
    try:
        items = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for i, item in enumerate(items):
            if i >= _MAX_LIST_ENTRIES:
                entries.append(f"... and {len(list(resolved.iterdir())) - _MAX_LIST_ENTRIES} more")
                break
            if item.is_dir():
                entries.append(f"📁 {item.name}/")
            else:
                size = item.stat().st_size
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f}MB"
                entries.append(f"📄 {item.name} ({size_str})")
    except PermissionError:
        return f"Error: Permission denied: {path}"

    if not entries:
        return f"Directory is empty: {path}"

    return "\n".join(entries)


@mcp.tool()
async def read_file(path: str, max_lines: int = 0) -> str:
    """Read the contents of a file.

    Args:
        path: File path relative to workspace root.
        max_lines: Maximum number of lines to read. 0 = entire file.

    Returns:
        File contents as text.
    """
    resolved = _resolve_safe(path)

    if not resolved.exists():
        return f"Error: File does not exist: {path}"
    if not resolved.is_file():
        return f"Error: Not a regular file: {path}"

    # Check size
    size = resolved.stat().st_size
    if size > _MAX_READ_SIZE:
        return (
            f"Error: File is too large ({size / (1024 * 1024):.1f}MB). "
            f"Max allowed: {_MAX_READ_SIZE / (1024 * 1024):.1f}MB. "
            f"Use max_lines to read a portion."
        )

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
        if max_lines > 0:
            lines = content.splitlines()
            content = "\n".join(lines[:max_lines])
            if len(lines) > max_lines:
                content += f"\n... ({len(lines) - max_lines} more lines)"
        return content
    except Exception as e:
        return f"Error reading file: {e}"


@mcp.tool()
async def search_files(pattern: str, path: str = ".", max_results: int = 50) -> str:
    """Search for files matching a name pattern.

    Args:
        pattern: Glob pattern to match filenames (e.g., '*.py', 'test_*').
        path: Directory to search in. Defaults to workspace root.
        max_results: Maximum number of results to return.

    Returns:
        List of matching file paths.
    """
    resolved = _resolve_safe(path)

    if not resolved.exists() or not resolved.is_dir():
        return f"Error: Directory does not exist: {path}"

    matches: list[str] = []
    root = _get_root()

    try:
        for item in resolved.rglob("*"):
            if fnmatch.fnmatch(item.name, pattern):
                rel_path = item.relative_to(root)
                prefix = "📁" if item.is_dir() else "📄"
                matches.append(f"{prefix} {rel_path}")
                if len(matches) >= max_results:
                    break
    except PermissionError:
        pass

    if not matches:
        return f"No files matching '{pattern}' found in {path}"

    result = f"Found {len(matches)} match(es):\n" + "\n".join(matches)
    if len(matches) >= max_results:
        result += f"\n(results capped at {max_results})"
    return result


@mcp.tool()
async def write_file(path: str, content: str, create_dirs: bool = True) -> str:
    """Write content to a file. Creates the file if it doesn't exist.

    Args:
        path: File path relative to workspace root.
        content: Content to write to the file.
        create_dirs: Whether to create parent directories if needed.

    Returns:
        Confirmation message.
    """
    resolved = _resolve_safe(path)

    try:
        if create_dirs:
            resolved.parent.mkdir(parents=True, exist_ok=True)

        resolved.write_text(content, encoding="utf-8")
        size = resolved.stat().st_size
        return f"Successfully wrote {size} bytes to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


@mcp.tool()
async def move_path(source: str, destination: str) -> str:
    """Move or rename a file or directory.

    Args:
        source: Source path relative to workspace root.
        destination: Destination path relative to workspace root.

    Returns:
        Confirmation message.
    """
    src = _resolve_safe(source)
    dst = _resolve_safe(destination)

    if not src.exists():
        return f"Error: Source does not exist: {source}"

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved {source} → {destination}"
    except Exception as e:
        return f"Error moving path: {e}"


@mcp.tool()
async def delete_path(path: str) -> str:
    """Delete a file or directory.

    Args:
        path: Path to delete, relative to workspace root.

    Returns:
        Confirmation message.
    """
    resolved = _resolve_safe(path)

    if not resolved.exists():
        return f"Error: Path does not exist: {path}"

    try:
        if resolved.is_dir():
            shutil.rmtree(resolved)
            return f"Deleted directory: {path}"
        else:
            resolved.unlink()
            return f"Deleted file: {path}"
    except Exception as e:
        return f"Error deleting path: {e}"
