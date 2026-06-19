"""Tests for file watcher (watcher.py)."""

from pathlib import Path

from filekb.watcher import (
    _should_exclude,
    compute_sha256,
    detect_changes,
    scan_directory,
)


class TestComputeSHA256:
    def test_deterministic(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        h1 = compute_sha256(f)
        h2 = compute_sha256(f)
        assert h1 == h2
        assert len(h1) == 64

    def test_content_sensitive(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        h1 = compute_sha256(f)
        f.write_text("world")
        h2 = compute_sha256(f)
        assert h1 != h2


class TestScanDirectory:
    def test_flat_directory(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        result = scan_directory(tmp_path, recursive=False, exclude_patterns=set())
        assert len(result) == 2

    def test_recursive(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("b")
        result = scan_directory(tmp_path, recursive=True, exclude_patterns=set())
        assert len(result) == 2

    def test_exclude_git(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("config")
        result = scan_directory(tmp_path, recursive=True)
        assert len(result) == 1


class TestDetectChanges:
    def test_added(self):
        changes = detect_changes({"a": "hash_a"}, {})
        assert changes["added"] == ["a"]
        assert changes["modified"] == []
        assert changes["deleted"] == []

    def test_modified(self):
        changes = detect_changes({"a": "hash_v2"}, {"a": "hash_v1"})
        assert changes["modified"] == ["a"]
        assert changes["added"] == []

    def test_deleted(self):
        changes = detect_changes({}, {"a": "hash_a"})
        assert changes["deleted"] == ["a"]

    def test_unchanged(self):
        changes = detect_changes({"a": "hash_same"}, {"a": "hash_same"})
        assert changes["unchanged"] == ["a"]


class TestShouldExclude:
    def test_git_excluded(self):
        assert _should_exclude(Path("/project/.git/config"), {".git"}) is True

    def test_node_modules_excluded(self):
        assert _should_exclude(Path("/project/node_modules/pkg"), {"node_modules"}) is True

    def test_normal_file_not_excluded(self):
        assert _should_exclude(Path("/project/src/main.py"), {".git"}) is False
