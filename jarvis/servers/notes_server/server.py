"""Notes MCP Server for JARVIS.

Tools:
    create_note: Create a new markdown note.
    append_note: Append content to an existing note.
    get_note: Read a specific note.
    list_notes: List all notes.
    search_notes: Search notes by content.

Notes are stored as markdown files in the configured NOTES_DIR.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Notes")

_NOTES_DIR: Path | None = None
_MAX_NOTES_LIST = 200


def _get_notes_dir() -> Path:
    """Get the notes directory, lazily initialized."""
    global _NOTES_DIR
    if _NOTES_DIR is None:
        notes_dir = os.environ.get("NOTES_DIR", "")
        if not notes_dir:
            # Default to ~/notes
            notes_dir = os.path.expanduser("~/notes")
        _NOTES_DIR = Path(notes_dir).expanduser().resolve()
        _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return _NOTES_DIR


def _sanitize_name(name: str) -> str:
    """Sanitize a note name for use as a filename."""
    # Remove dangerous characters
    safe = re.sub(r'[^\w\s\-.]', '', name)
    safe = safe.strip()
    if not safe:
        safe = "untitled"
    # Ensure .md extension
    if not safe.endswith(".md"):
        safe += ".md"
    return safe


def _note_path(name: str) -> Path:
    """Resolve a note name to a file path."""
    sanitized = _sanitize_name(name)
    return _get_notes_dir() / sanitized


@mcp.tool()
async def create_note(name: str, content: str) -> str:
    """Create a new markdown note.

    Args:
        name: Note name (will be used as filename, .md appended if needed).
        content: Note content in markdown format.

    Returns:
        Confirmation message.
    """
    path = _note_path(name)

    if path.exists():
        return f"Error: Note '{name}' already exists. Use append_note to add content."

    # Add metadata header
    now = datetime.now(timezone.utc).isoformat()
    full_content = f"# {name.replace('.md', '')}\n\n_Created: {now}_\n\n{content}\n"

    try:
        path.write_text(full_content, encoding="utf-8")
        return f"Created note: {path.name}"
    except Exception as e:
        return f"Error creating note: {e}"


@mcp.tool()
async def append_note(name: str, content: str) -> str:
    """Append content to an existing note.

    Args:
        name: Name of the note to append to.
        content: Content to append.

    Returns:
        Confirmation message.
    """
    path = _note_path(name)

    if not path.exists():
        return f"Error: Note '{name}' does not exist. Use create_note first."

    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n_Updated: {now}_\n\n{content}\n")
        return f"Appended to note: {path.name}"
    except Exception as e:
        return f"Error appending to note: {e}"


@mcp.tool()
async def get_note(name: str) -> str:
    """Read a specific note.

    Args:
        name: Name of the note to read.

    Returns:
        Note contents.
    """
    path = _note_path(name)

    if not path.exists():
        # Try fuzzy match
        notes_dir = _get_notes_dir()
        candidates = [
            f.name for f in notes_dir.glob("*.md")
            if name.lower() in f.name.lower()
        ]
        if candidates:
            return (
                f"Note '{name}' not found. Did you mean one of these?\n"
                + "\n".join(f"  - {c}" for c in candidates[:5])
            )
        return f"Note '{name}' not found."

    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading note: {e}"


@mcp.tool()
async def list_notes() -> str:
    """List all notes.

    Returns:
        Formatted list of all notes with sizes and modification dates.
    """
    notes_dir = _get_notes_dir()

    notes = sorted(notes_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not notes:
        return "No notes found."

    entries: list[str] = []
    for i, note in enumerate(notes):
        if i >= _MAX_NOTES_LIST:
            entries.append(f"... and {len(notes) - _MAX_NOTES_LIST} more")
            break
        stat = note.stat()
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        size_kb = stat.st_size / 1024
        entries.append(f"📝 {note.name} ({size_kb:.1f}KB, modified: {modified})")

    return f"Notes ({len(notes)} total):\n" + "\n".join(entries)


@mcp.tool()
async def search_notes(query: str, max_results: int = 20) -> str:
    """Search notes by content.

    Args:
        query: Text to search for (case-insensitive).
        max_results: Maximum results to return.

    Returns:
        Matching notes with context snippets.
    """
    notes_dir = _get_notes_dir()
    query_lower = query.lower()
    matches: list[str] = []

    for note in sorted(notes_dir.glob("*.md")):
        try:
            content = note.read_text(encoding="utf-8")
            if query_lower in content.lower():
                # Find context around the match
                idx = content.lower().index(query_lower)
                start = max(0, idx - 50)
                end = min(len(content), idx + len(query) + 50)
                snippet = content[start:end].replace("\n", " ").strip()
                if start > 0:
                    snippet = "..." + snippet
                if end < len(content):
                    snippet = snippet + "..."
                matches.append(f"📝 {note.name}: {snippet}")

                if len(matches) >= max_results:
                    break
        except Exception:
            continue

    if not matches:
        return f"No notes matching '{query}' found."

    result = f"Found {len(matches)} matching note(s):\n" + "\n".join(matches)
    return result
