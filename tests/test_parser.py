"""Tests for document parser (parser.py)."""

import pytest

from filekb.parser import is_supported, parse_file


class TestIsSupported:
    def test_markdown_supported(self):
        assert is_supported("doc.md") is True

    def test_pdf_supported(self):
        assert is_supported("doc.pdf") is True

    def test_python_supported(self):
        assert is_supported("main.py") is True

    def test_unknown_unsupported(self):
        assert is_supported("file.xyz") is False


class TestParseText:
    def test_markdown(self, sample_md):
        text = parse_file(sample_md)
        assert "Alice" in text
        assert "Acme Corp" in text

    def test_chinese_markdown(self, sample_zh_md):
        text = parse_file(sample_zh_md)
        assert "张三" in text
        assert "华为技术有限公司" in text

    def test_csv(self, sample_csv):
        text = parse_file(sample_csv)
        assert "name" in text
        assert "Alice" in text

    def test_json(self, sample_json):
        text = parse_file(sample_json)
        assert "Alpha" in text
        assert "Alice" in text


class TestParseCode:
    def test_python_file(self, sample_py):
        text = parse_file(sample_py)
        # Should contain class/function signatures
        assert "DataProcessor" in text
        assert "__init__" in text

    def test_python_no_body(self, sample_py):
        """Python parser extracts signatures, not full bodies."""
        text = parse_file(sample_py)
        # Implementation details should not appear
        assert "# Implementation details omitted" not in text


class TestParseErrors:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            parse_file("/nonexistent/file.md")

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "test.xyz"
        f.write_text("data")
        with pytest.raises(ValueError, match="Unsupported file type"):
            parse_file(f)
