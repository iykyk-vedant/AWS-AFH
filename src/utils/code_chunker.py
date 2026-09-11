"""
Code Chunker for Amaze on Work.

Splits source files by AST boundaries (function/class definitions) so only
the relevant chunk is sent to the LLM — preventing context overflow on large files.

Ported from the tata-lcr project (src/ingestion/chunker.py).
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CodeChunk:
    """A semantically meaningful slice of a source file."""
    content: str
    start_line: int
    end_line: int
    chunk_type: str   # 'function', 'class', 'module', 'block', 'full', 'continuation'
    name: str | None = None

    def contains_line(self, line_no: int) -> bool:
        """Check if a given line number falls within this chunk."""
        return self.start_line <= line_no <= self.end_line

    def summary(self) -> str:
        kind = f"{self.chunk_type} {self.name}" if self.name else self.chunk_type
        return f"[{kind} L{self.start_line}-{self.end_line}]"


class CodeChunker:
    """
    Splits source code into semantic chunks.

    For Python: splits at def / class / async def boundaries.
    For all other languages: splits by line-count blocks.
    """

    DEFAULT_MAX_LINES = 500
    DEFAULT_MAX_CHARS = 50_000

    def __init__(self, max_lines: int = DEFAULT_MAX_LINES, max_chars: int = DEFAULT_MAX_CHARS):
        self.max_lines = max_lines
        self.max_chars = max_chars

    def needs_chunking(self, content: str) -> bool:
        lines = content.count("\n") + 1
        return lines > self.max_lines or len(content) > self.max_chars

    def chunk_by_structure(self, content: str, language: str) -> list[CodeChunk]:
        """Chunk content using language-appropriate strategy."""
        if language in ("python", "py"):
            return self._chunk_python(content)
        return self._chunk_by_lines(content)

    def get_chunk_for_line(self, content: str, language: str, line_no: int) -> CodeChunk:
        """
        Return the single chunk that contains line_no.
        Falls back to returning the full content as one chunk if not found.
        """
        chunks = self.chunk_by_structure(content, language)
        for chunk in chunks:
            if chunk.contains_line(line_no):
                return chunk
        # Fallback: return entire file as one chunk
        return CodeChunk(
            content=content,
            start_line=1,
            end_line=content.count("\n") + 1,
            chunk_type="full",
            name=None,
        )

    # ── Internal chunkers ─────────────────────────────────────────

    def _chunk_python(self, content: str) -> list[CodeChunk]:
        chunks = []
        lines = content.split("\n")

        current_chunk_lines: list[str] = []
        current_start = 1
        current_type = "module"
        current_name = None

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            is_definition = (
                stripped.startswith("def ")
                or stripped.startswith("class ")
                or stripped.startswith("async def ")
            )

            if is_definition and current_chunk_lines:
                chunks.append(CodeChunk(
                    content="\n".join(current_chunk_lines),
                    start_line=current_start,
                    end_line=current_start + len(current_chunk_lines) - 1,
                    chunk_type=current_type,
                    name=current_name,
                ))
                current_chunk_lines = []
                current_start = i + 1

            if stripped.startswith("class "):
                current_type = "class"
                current_name = stripped[6:].split("(")[0].split(":")[0].strip()
            elif stripped.startswith("def ") or stripped.startswith("async def "):
                current_type = "function"
                name_start = 4 if stripped.startswith("def ") else 10
                current_name = stripped[name_start:].split("(")[0].strip()

            current_chunk_lines.append(line)

            # Force-split if chunk grows beyond max_lines
            if len(current_chunk_lines) >= self.max_lines:
                chunks.append(CodeChunk(
                    content="\n".join(current_chunk_lines),
                    start_line=current_start,
                    end_line=current_start + len(current_chunk_lines) - 1,
                    chunk_type=current_type,
                    name=current_name,
                ))
                current_chunk_lines = []
                current_start = i + 2
                current_type = "continuation"
                current_name = None

            i += 1

        if current_chunk_lines:
            chunks.append(CodeChunk(
                content="\n".join(current_chunk_lines),
                start_line=current_start,
                end_line=current_start + len(current_chunk_lines) - 1,
                chunk_type=current_type,
                name=current_name,
            ))

        return chunks

    def _chunk_by_lines(self, content: str) -> list[CodeChunk]:
        chunks = []
        lines = content.split("\n")
        for i in range(0, len(lines), self.max_lines):
            chunk_lines = lines[i: i + self.max_lines]
            chunks.append(CodeChunk(
                content="\n".join(chunk_lines),
                start_line=i + 1,
                end_line=i + len(chunk_lines),
                chunk_type="block",
                name=None,
            ))
        return chunks


def detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".java": "java",
        ".go": "go",
        ".rb": "ruby",
        ".rs": "rust",
    }
    return mapping.get(ext, "text")
