"""Tests for CLI dispatch — verifies main() returns exit codes without sys.exit."""
from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from superhuman_mail._envelope import emit, error, fail, ok
from superhuman_mail.cli import main


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

    def test_comment_read_many_dispatches_all_thread_ids(self, capsys):
        result = {
            "status": "succeeded",
            "command": "comment.read-many",
            "data": {"complete": True, "comments_by_thread": {}},
            "errors": [],
            "warnings": [],
        }
        with patch("superhuman_mail.cli._comment.read_many", return_value=result) as read_many:
            code = main(["comment", "read-many", "thread-a", "thread-b", "--batch-size", "1"])
        assert code == 0
        read_many.assert_called_once_with(["thread-a", "thread-b"], batch_size=1)
        assert json.loads(capsys.readouterr().out)["command"] == "comment.read-many"

    def test_setup_passes_email(self, capsys):
        with patch("superhuman_mail.cli._setup.run_setup", return_value={"config": {}, "path": "/tmp/config.json", "steps": []}) as run_setup:
            code = main(["setup", "--config", "/tmp/config.json", "--email", "chosen@example.com"])
        assert code == 0
        run_setup.assert_called_once()
        assert run_setup.call_args.kwargs["email"] == "chosen@example.com"
        assert str(run_setup.call_args.kwargs["config_path"]) == "/tmp/config.json"
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "succeeded"

    def test_thread_messages_defaults_to_configured_account(self, capsys):
        result = {
            "status": "succeeded",
            "command": "thread.messages",
            "data": {"thread_id": "t1", "message_count": 0, "messages": []},
            "errors": [],
            "warnings": [],
        }
        with patch("superhuman_mail.cli._thread.messages", return_value=result) as messages:
            code = main(["thread", "messages", "t1"])
        assert code == 0
        messages.assert_called_once_with("t1", account=None)

    def test_thread_messages_passes_account(self, capsys):
        result = {
            "status": "succeeded",
            "command": "thread.messages",
            "data": {"thread_id": "t1", "message_count": 0, "messages": []},
            "errors": [],
            "warnings": [],
        }
        with patch("superhuman_mail.cli._thread.messages", return_value=result) as messages:
            code = main(["thread", "messages", "t1", "--account", "second@example.com"])
        assert code == 0
        messages.assert_called_once_with("t1", account="second@example.com")
        assert json.loads(capsys.readouterr().out)["command"] == "thread.messages"

    def test_attachment_download_passes_output_selectors_and_account(self, capsys):
        result = {
            "status": "succeeded",
            "command": "attachment.download",
            "data": {"thread_id": "t1", "attachment_count": 1},
            "errors": [],
            "warnings": [],
        }
        with patch(
            "superhuman_mail.cli._attachment.download",
            return_value=result,
        ) as download:
            code = main([
                "attachment",
                "download",
                "t1",
                "--output",
                "/tmp/files",
                "--account",
                "second@example.com",
                "--message-id",
                "m1",
                "--attachment-id",
                "a1",
            ])
        assert code == 0
        download.assert_called_once_with(
            "t1",
            "/tmp/files",
            account="second@example.com",
            message_id="m1",
            attachment_id="a1",
        )
        assert json.loads(capsys.readouterr().out)["command"] == "attachment.download"

    def test_attachment_download_requires_output(self, capsys):
        try:
            main(["attachment", "download", "t1"])
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("attachment download accepted no output directory")
        out = json.loads(capsys.readouterr().out)
        assert out["errors"][0]["code"] == "INVALID_ARGS"
        assert out["command"] == "attachment.download"

    def test_attachment_help_states_local_metadata_boundary(self, capsys):
        try:
            main(["attachment", "download", "--help"])
        except SystemExit as exc:
            assert exc.code == 0
        else:
            raise AssertionError("attachment help did not exit")
        help_text = capsys.readouterr().out
        assert "local sync cache" in help_text
        assert "do not need to be cached or previously opened" in help_text
