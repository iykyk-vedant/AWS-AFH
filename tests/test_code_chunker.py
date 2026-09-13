"""
Unit tests for the Code Chunker utility.

Validates AST-based function/class boundary splitting for Python files
and line-count block splitting for other languages.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.code_chunker import CodeChunker, CodeChunk


class TestCodeChunkerBasic:
    """Basic chunking behavior."""

    def test_short_file_no_chunking_needed(self):
        """Files shorter than max_lines should not need chunking."""
        chunker = CodeChunker(max_lines=500, max_chars=50_000)
        short_code = "x = 1\ny = 2\n"
        assert chunker.needs_chunking(short_code) is False

    def test_long_file_needs_chunking(self):
        """Files longer than max_lines should trigger chunking."""
        chunker = CodeChunker(max_lines=10)
        long_code = "\n".join(f"line_{i} = {i}" for i in range(50))
        assert chunker.needs_chunking(long_code) is True

    def test_large_chars_needs_chunking(self):
        """Files exceeding max_chars should trigger chunking."""
        chunker = CodeChunker(max_lines=999999, max_chars=100)
        large_code = "x = 1\n" * 50  # 300 chars
        assert chunker.needs_chunking(large_code) is True


class TestCodeChunkContainsLine:
    """Verify CodeChunk.contains_line boundary checks."""

    def test_contains_line_start_boundary(self):
        chunk = CodeChunk(content="x = 1", start_line=10, end_line=20, chunk_type="block")
        assert chunk.contains_line(10) is True

    def test_contains_line_end_boundary(self):
        chunk = CodeChunk(content="x = 1", start_line=10, end_line=20, chunk_type="block")
        assert chunk.contains_line(20) is True

    def test_contains_line_mid(self):
        chunk = CodeChunk(content="x = 1", start_line=10, end_line=20, chunk_type="block")
        assert chunk.contains_line(15) is True

    def test_does_not_contain_line_before(self):
        chunk = CodeChunk(content="x = 1", start_line=10, end_line=20, chunk_type="block")
        assert chunk.contains_line(9) is False

    def test_does_not_contain_line_after(self):
        chunk = CodeChunk(content="x = 1", start_line=10, end_line=20, chunk_type="block")
        assert chunk.contains_line(21) is False


class TestPythonChunking:
    """Verify Python AST-based chunking."""

    def test_single_function_produces_one_chunk(self):
        code = "def hello():\n    return 'world'\n"
        chunker = CodeChunker(max_lines=1)  # force chunking
        chunks = chunker.chunk_by_structure(code, "python")
        # Should produce at least one chunk containing the function
        assert len(chunks) >= 1
        func_chunks = [c for c in chunks if c.chunk_type == "function"]
        assert len(func_chunks) >= 1
        assert func_chunks[0].name == "hello"

    def test_multiple_functions_produce_separate_chunks(self):
        code = (
            "def foo():\n    return 1\n\n"
            "def bar():\n    return 2\n"
        )
        chunker = CodeChunker(max_lines=1)
        chunks = chunker.chunk_by_structure(code, "python")
        func_names = [c.name for c in chunks if c.chunk_type == "function"]
        assert "foo" in func_names
        assert "bar" in func_names

    def test_class_produces_chunk(self):
        code = "class MyClass:\n    def method(self):\n        pass\n"
        chunker = CodeChunker(max_lines=1)
        chunks = chunker.chunk_by_structure(code, "python")
        class_chunks = [c for c in chunks if c.chunk_type == "class"]
        assert len(class_chunks) >= 1
        assert class_chunks[0].name == "MyClass"


class TestChunkSummary:
    """Verify CodeChunk.summary() formatting."""

    def test_summary_with_name(self):
        chunk = CodeChunk(content="", start_line=1, end_line=10, chunk_type="function", name="foo")
        s = chunk.summary()
        assert "function foo" in s
        assert "L1-10" in s

    def test_summary_without_name(self):
        chunk = CodeChunk(content="", start_line=5, end_line=25, chunk_type="block")
        s = chunk.summary()
        assert "block" in s
        assert "L5-25" in s
