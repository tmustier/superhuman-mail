"""Tests for CLI dispatch — verifies main() returns exit codes without sys.exit."""
from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from superhuman_mail.cli import main
from superhuman_mail._envelope import emit, ok, fail, error


class TestEmitReturnsExitCode:
    def test_success_returns_zero(self):
        buf = StringIO()
        with patch("superhuman_mail._envelope.sys.stdout", buf):
            code = emit(ok("test", {"hello": "world"}))
        assert code == 0
        assert json.loads(buf.getvalue())["status"] == "succeeded"

    def test_failure_returns_one(self):
        buf = StringIO()
        with patch("superhuman_mail._envelope.sys.stdout", buf):
            code = emit(fail("test", [error("input", "BAD", False, "oops")]))
        assert code == 1

    def test_custom_exit_code(self):
        buf = StringIO()
        with patch("superhuman_mail._envelope.sys.stdout", buf):
            code = emit(ok("test", {}), exit_code=3)
        assert code == 3


class TestMainReturnsExitCode:
    def test_schema_returns_zero(self, capsys):
        code = main(["schema"])
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "succeeded"
        assert "commands" in out["data"]

    def test_schema_specific_command(self, capsys):
        code = main(["schema", "doctor"])
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["data"]["safety"] == "read"

    def test_schema_unknown_command(self, capsys):
        code = main(["schema", "nonexistent.command"])
        assert code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "failed"

    def test_no_command_returns_one(self, capsys):
        code = main([])
        assert code == 1

    def test_missing_subaction(self, capsys):
        code = main(["thread"])
        assert code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "failed"
        assert "MISSING_ACTION" in out["errors"][0]["code"]
