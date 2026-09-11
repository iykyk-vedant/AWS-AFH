"""
Unified diff / patch generation utility.

Generates standard unified diff format patches from code changes.
"""

import difflib
from dataclasses import dataclass


@dataclass
class FileChange:
    """A single file change."""
    file_path: str
    original_content: str
    modified_content: str
    rationale: str = ""


def generate_unified_diff(
    original: str,
    modified: str,
    file_path: str,
    context_lines: int = 3,
) -> str:
    """Generate a unified diff between original and modified content."""
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)

    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=context_lines,
    )

    return "".join(diff)


def generate_patch(changes: list[FileChange]) -> str:
    """Generate a combined patch from multiple file changes."""
    patches = []
    for change in changes:
        diff = generate_unified_diff(
            change.original_content,
            change.modified_content,
            change.file_path,
        )
        if diff:
            patches.append(diff)

    return "\n".join(patches)


def count_changed_lines(patch: str) -> int:
    """Count the number of added + removed lines in a patch."""
    added = 0
    removed = 0
    for line in patch.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added + removed


def parse_patch_files(patch: str) -> list[str]:
    """Extract file paths modified by a patch."""
    files = []
    for line in patch.split("\n"):
        if line.startswith("+++ b/"):
            files.append(line[6:])
    return files
