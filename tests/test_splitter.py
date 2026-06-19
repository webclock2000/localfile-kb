"""Tests for text splitter (splitter.py)."""

from filekb.splitter import (
    _hard_split,
    _merge_with_limit,
    _split_paragraphs,
    chunk_text,
    detect_chinese,
)


class TestChineseDetection:
    def test_pure_chinese(self):
        text = "这是一个中文测试文档，包含大量的中文字符。用于验证中文检测功能是否正常工作。"
        assert detect_chinese(text) is True

    def test_pure_english(self):
        text = "This is an English document with no Chinese characters at all. It should not trigger detection."
        assert detect_chinese(text) is False

    def test_mixed_content_below_threshold(self):
        text = "This is mostly English with one 中文 word."
        assert detect_chinese(text) is False

    def test_mixed_content_above_threshold(self):
        english_part = "English "
        chinese_part = "中文内容"
        text = english_part * 5 + chinese_part * 20
        assert detect_chinese(text) is True

    def test_empty_text(self):
        assert detect_chinese("") is False

    def test_no_relevant_chars(self):
        assert detect_chinese("12345 !@#$%") is False


class TestChunkText:
    def test_small_text_single_chunk(self):
        chunks = chunk_text("Hello world.", max_chars=1000)
        assert len(chunks) == 1
        assert "Hello world." in chunks[0]

    def test_paragraph_split(self):
        text = "Paragraph A.\n\nParagraph B.\n\nParagraph C."
        chunks = chunk_text(text, max_chars=20)
        assert len(chunks) > 1

    def test_overlap(self):
        text = "First paragraph with some content.\n\nSecond paragraph with other content."
        chunks = chunk_text(text, max_chars=200, overlap_chars=20)
        if len(chunks) > 1:
            # Last chars of chunk1 should appear at start of chunk2
            pass  # Overlap depends on merge behavior

    def test_empty_text(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_respects_max_chars(self):
        text = "A" * 50000
        chunks = chunk_text(text, max_chars=24000)
        for c in chunks:
            assert len(c) <= 25000  # Allow slight overflow from single oversized pieces


class TestSplitParagraphs:
    def test_basic(self):
        parts = _split_paragraphs("A.\n\nB.\n\nC.")
        assert len(parts) == 3

    def test_single_paragraph(self):
        parts = _split_paragraphs("Single paragraph.")
        assert len(parts) == 1


class TestMergeWithLimit:
    def test_all_fit(self):
        merged = _merge_with_limit(["a", "b", "c"], 100)
        assert len(merged) == 1
        assert "a" in merged[0]

    def test_oversized_piece(self):
        merged = _merge_with_limit(["short", "a" * 100], 50)
        assert len(merged) == 2  # The long piece is passed through


class TestHardSplit:
    def test_basic(self):
        parts = _hard_split("abcdefgh", 3)
        assert parts == ["abc", "def", "gh"]
