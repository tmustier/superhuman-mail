"""Tests for version extraction from LevelDB files."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from superhuman_mail.setup import extract_version, _LEVELDB_DIR


def _write_ldb(tmpdir: Path, filename: str, content: str) -> None:
    (tmpdir / filename).write_bytes(content.encode("latin-1"))


class TestExtractVersion:
    def test_anchored_version_preferred(self, tmp_path):
        """When lastCodeVersion key is present, use it instead of random timestamps."""
        _write_ldb(tmp_path, "001.ldb", (
            "analytics_ts\x002026-03-20T12:00:00Z"  # decoy — newer
            "\x00lastCodeVersion\x002026-01-15T08:30:00Z"  # real version
        ))
        with patch("superhuman_mail.setup._LEVELDB_DIR", tmp_path):
            assert extract_version() == "2026-01-15T08:30:00Z"

    def test_fallback_to_unanchored(self, tmp_path):
        """Without lastCodeVersion key, falls back to latest timestamp."""
        _write_ldb(tmp_path, "001.ldb", (
            "some_field\x002025-12-01T00:00:00Z"
            "\x00other\x002026-03-20T12:00:00Z"
        ))
        with patch("superhuman_mail.setup._LEVELDB_DIR", tmp_path):
            assert extract_version() == "2026-03-20T12:00:00Z"

    def test_anchored_picks_latest(self, tmp_path):
        """Multiple lastCodeVersion entries — picks the latest."""
        _write_ldb(tmp_path, "001.ldb", "lastCodeVersion\x002025-06-01T00:00:00Z")
        _write_ldb(tmp_path, "002.ldb", "lastCodeVersion\x002026-01-15T08:30:00Z")
        with patch("superhuman_mail.setup._LEVELDB_DIR", tmp_path):
            assert extract_version() == "2026-01-15T08:30:00Z"

    def test_no_timestamps_raises(self, tmp_path):
        _write_ldb(tmp_path, "001.ldb", "nothing useful here")
        with patch("superhuman_mail.setup._LEVELDB_DIR", tmp_path):
            with pytest.raises(RuntimeError, match="Could not find version"):
                extract_version()

    def test_missing_dir_raises(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with patch("superhuman_mail.setup._LEVELDB_DIR", missing):
            with pytest.raises(RuntimeError, match="not found"):
                extract_version()
