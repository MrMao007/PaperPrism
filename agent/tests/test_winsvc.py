"""Unit tests for winsvc.py — Windows Task Scheduler helpers.

All tests run on every platform (macOS / Linux / Windows) by mocking
subprocess and filesystem. No real schtasks.exe is invoked.
"""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from paperprism_agent import winsvc as w
from paperprism_agent.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path) -> Config:
    cfg = Config.from_args(home=str(tmp_path))
    cfg.paths.ensure()
    return cfg


def _mock_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ---------------------------------------------------------------------------
# resolve_launcher
# ---------------------------------------------------------------------------

class TestResolveLauncher:
    def test_returns_list_of_strings(self):
        result = w.resolve_launcher()
        assert isinstance(result, list)
        assert all(isinstance(item, str) for item in result)
        assert len(result) >= 1

    def test_frozen_binary_returns_sys_executable_only(self, monkeypatch: pytest.MonkeyPatch):
        import sys
        monkeypatch.setattr("sys.frozen", True, raising=False)
        result = w.resolve_launcher()
        assert result == [sys.executable]

    def test_uvx_cache_raises_runtime_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import sys
        uvx_cache = tmp_path / "AppData" / "Local" / "uv" / "cache" / "bin" / "python.exe"
        uvx_cache.parent.mkdir(parents=True)
        uvx_cache.touch()
        monkeypatch.setattr(sys, "executable", str(uvx_cache))
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with pytest.raises(RuntimeError, match="ephemeral"):
            w.resolve_launcher()


# ---------------------------------------------------------------------------
# write_wrapper_bat
# ---------------------------------------------------------------------------

class TestWriteWrapperBat:
    def test_creates_bat_file(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        bat = w.write_wrapper_bat(cfg)
        assert bat.exists()
        assert bat.suffix == ".bat"

    def test_bat_contains_paperprism_home(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        bat = w.write_wrapper_bat(cfg)
        content = bat.read_text()
        assert f"PAPERPRISM_HOME={tmp_path}" in content

    def test_bat_contains_host_and_port(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        bat = w.write_wrapper_bat(cfg)
        content = bat.read_text()
        assert "PAPERPRISM_HOST=127.0.0.1" in content
        assert "PAPERPRISM_PORT=17321" in content

    def test_bat_contains_token_when_set(self, tmp_path: Path):
        cfg = Config.from_args(home=str(tmp_path), token="mytoken")
        cfg.paths.ensure()
        bat = w.write_wrapper_bat(cfg)
        content = bat.read_text()
        assert "PAPERPRISM_TOKEN=mytoken" in content

    def test_bat_excludes_token_when_empty(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        bat = w.write_wrapper_bat(cfg)
        content = bat.read_text()
        assert "PAPERPRISM_TOKEN" not in content

    def test_bat_injects_secrets(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        secrets_file = cfg.paths.secrets_file
        secrets_file.write_text("OPENAI_API_KEY=sk-win-test\n")
        secrets_file.chmod(0o600)
        bat = w.write_wrapper_bat(cfg)
        content = bat.read_text()
        assert "OPENAI_API_KEY=sk-win-test" in content

    def test_bat_starts_with_echo_off(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        bat = w.write_wrapper_bat(cfg)
        first_line = bat.read_text().splitlines()[0]
        assert "@echo off" in first_line


# ---------------------------------------------------------------------------
# schtasks wrappers (all mock subprocess)
# ---------------------------------------------------------------------------

class TestSchtasksWrappers:
    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_create_task_calls_schtasks_create(self, mock_run, tmp_path: Path):
        mock_run.return_value = _mock_run()
        cfg = _make_cfg(tmp_path)
        w.create_task(cfg)
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert any("schtasks" in call[0] and "/Create" in call for call in calls)

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_create_task_uses_onlogon_trigger(self, mock_run, tmp_path: Path):
        mock_run.return_value = _mock_run()
        cfg = _make_cfg(tmp_path)
        w.create_task(cfg)
        all_args = " ".join(
            " ".join(c.args[0]) for c in mock_run.call_args_list
        )
        assert "ONLOGON" in all_args

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_delete_task(self, mock_run):
        mock_run.return_value = _mock_run()
        w.delete_task()
        mock_run.assert_called_once_with(
            ["schtasks", "/Delete", "/TN", w.TASK_NAME, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_end_task(self, mock_run):
        mock_run.return_value = _mock_run()
        w.end_task()
        mock_run.assert_called_once_with(
            ["schtasks", "/End", "/TN", w.TASK_NAME],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_run_task_now(self, mock_run):
        mock_run.return_value = _mock_run()
        w.run_task_now()
        mock_run.assert_called_once_with(
            ["schtasks", "/Run", "/TN", w.TASK_NAME],
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_is_running_true(self, mock_run):
        mock_run.return_value = _mock_run(returncode=0, stdout='"PaperPrismAgent","N/A","Running"')
        assert w.is_running() is True

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_is_running_false_when_ready(self, mock_run):
        mock_run.return_value = _mock_run(returncode=0, stdout='"PaperPrismAgent","N/A","Ready"')
        assert w.is_running() is False

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_is_running_false_when_not_found(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1, stderr="ERROR: task not found")
        assert w.is_running() is False

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_task_exists_true(self, mock_run):
        mock_run.return_value = _mock_run(returncode=0, stdout="PaperPrismAgent")
        assert w.task_exists() is True

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_task_exists_false(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1, stderr="not found")
        assert w.task_exists() is False

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_print_status_ok(self, mock_run):
        mock_run.return_value = _mock_run(returncode=0, stdout="Status: Ready")
        code, text = w.print_status()
        assert code == 0
        assert "Ready" in text

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_print_status_not_found(self, mock_run):
        mock_run.return_value = _mock_run(returncode=1, stderr="ERROR: not found")
        code, text = w.print_status()
        assert code == 1
        assert "not found" in text

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_restart_ends_then_runs(self, mock_run):
        mock_run.return_value = _mock_run()
        w.restart()
        calls = [c.args[0] for c in mock_run.call_args_list]
        # Should call /End first, then /Run
        assert any("/End" in call for call in calls)
        assert any("/Run" in call for call in calls)
        end_idx = next(i for i, c in enumerate(calls) if "/End" in c)
        run_idx = next(i for i, c in enumerate(calls) if "/Run" in c)
        assert end_idx < run_idx


# ---------------------------------------------------------------------------
# load_secrets Windows permission fix (in launchd.py)
# ---------------------------------------------------------------------------

class TestWindowsSecretPermissionFix:
    """Verify that load_secrets() skips the chmod check on Windows."""

    def test_secrets_readable_on_windows_despite_wide_permissions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """On Windows stat returns 0o666 — we must NOT refuse the file."""
        from paperprism_agent.launchd import load_secrets

        secrets_file = tmp_path / "secrets.env"
        secrets_file.write_text("OPENAI_API_KEY=sk-test\n")
        # Don't chmod — leave default (simulates Windows-like broad perms)

        # Patch sys.platform to simulate Windows
        monkeypatch.setattr("paperprism_agent.launchd.sys.platform", "win32")

        secrets, warnings = load_secrets(secrets_file)
        # Should read successfully, not return empty due to permission refusal
        assert "OPENAI_API_KEY" in secrets
        assert secrets["OPENAI_API_KEY"] == "sk-test"
        # No permission-refusal warning
        assert not any("refusing to read" in w for w in warnings)

    def test_secrets_refused_on_posix_with_wide_permissions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """On POSIX, 0o644 permissions must still be refused."""
        from paperprism_agent.launchd import load_secrets

        secrets_file = tmp_path / "secrets.env"
        secrets_file.write_text("OPENAI_API_KEY=sk-test\n")
        secrets_file.chmod(0o644)  # group/world readable — should be refused

        monkeypatch.setattr("paperprism_agent.launchd.sys.platform", "linux")

        secrets, warnings = load_secrets(secrets_file)
        assert secrets == {}
        assert any("refusing to read" in w for w in warnings)


# ---------------------------------------------------------------------------
# _set_or_create (XML helper)
# ---------------------------------------------------------------------------

class TestSetOrCreate:
    """Unit tests for the _set_or_create XML helper."""

    def test_creates_child_when_absent(self):
        import xml.etree.ElementTree as ET
        ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
        parent = ET.Element(f"{{{ns}}}Settings")

        w._set_or_create(parent, f"{{{ns}}}NewElement", "hello", ns)

        child = parent.find(f"{{{ns}}}NewElement")
        assert child is not None
        assert child.text == "hello"

    def test_updates_existing_child(self):
        import xml.etree.ElementTree as ET
        ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
        parent = ET.Element(f"{{{ns}}}Settings")
        existing = ET.SubElement(parent, f"{{{ns}}}Period")
        existing.text = "PT10S"

        w._set_or_create(parent, f"{{{ns}}}Period", "PT30S", ns)

        children = list(parent.findall(f"{{{ns}}}Period"))
        assert len(children) == 1, "should not create a duplicate element"
        assert children[0].text == "PT30S"


# ---------------------------------------------------------------------------
# _apply_restart_on_failure
# ---------------------------------------------------------------------------

# Minimal Task Scheduler XML returned by `schtasks /Query /XML`.
_SAMPLE_TASK_XML = (
    '<?xml version="1.0" encoding="UTF-16"?>\n'
    '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
    "<RegistrationInfo/>"
    "<Triggers/>"
    "<Actions/>"
    "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy></Settings>"
    "</Task>"
)

# Same XML but with <RestartOnFailure> already present.
_SAMPLE_TASK_XML_WITH_RESTART = (
    '<?xml version="1.0" encoding="UTF-16"?>\n'
    '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
    "<RegistrationInfo/>"
    "<Triggers/>"
    "<Actions/>"
    "<Settings>"
    "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
    "<RestartOnFailure>"
    "<Period>PT10S</Period>"
    "<Count>1</Count>"
    "</RestartOnFailure>"
    "</Settings>"
    "</Task>"
)


class TestApplyRestartOnFailure:
    """Tests for _apply_restart_on_failure().

    All tests run on every platform via mocking — no schtasks.exe needed.
    """

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_injects_restart_on_failure_into_xml(self, mock_run):
        """Happy path: export XML → patch → re-import."""
        # First call: /Query /XML → return sample XML.
        # Second call: /Create /XML /F → success.
        mock_run.side_effect = [
            _mock_run(returncode=0, stdout=_SAMPLE_TASK_XML),
            _mock_run(returncode=0),
        ]

        w._apply_restart_on_failure(interval_seconds=30, count=3)

        assert mock_run.call_count == 2
        # Second call must be a /Create /XML
        second_args = mock_run.call_args_list[1].args[0]
        assert "schtasks" in second_args[0]
        assert "/Create" in second_args
        assert "/XML" in second_args
        assert "/F" in second_args

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_updates_existing_restart_on_failure(self, mock_run):
        """When <RestartOnFailure> already exists, values are updated."""
        mock_run.side_effect = [
            _mock_run(returncode=0, stdout=_SAMPLE_TASK_XML_WITH_RESTART),
            _mock_run(returncode=0),
        ]

        w._apply_restart_on_failure(interval_seconds=30, count=3)

        # Should still produce two subprocess calls (export + re-import).
        assert mock_run.call_count == 2

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_skips_gracefully_when_export_fails(self, mock_run):
        """If schtasks /Query /XML fails (non-zero), function is a no-op."""
        mock_run.return_value = _mock_run(returncode=1, stderr="ERROR: not found")

        # Must not raise; just log and return.
        w._apply_restart_on_failure()

        # Only one call attempted (the /Query); no re-import call.
        assert mock_run.call_count == 1

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_skips_gracefully_when_xml_is_empty(self, mock_run):
        """If schtasks /Query /XML returns empty stdout, function is a no-op."""
        mock_run.return_value = _mock_run(returncode=0, stdout="")

        w._apply_restart_on_failure()

        assert mock_run.call_count == 1

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_skips_gracefully_when_xml_is_invalid(self, mock_run):
        """Malformed XML from schtasks → log warning, do not raise."""
        mock_run.return_value = _mock_run(returncode=0, stdout="not valid xml <<<")

        w._apply_restart_on_failure()

        # Only the /Query call; no re-import attempted.
        assert mock_run.call_count == 1

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_skips_gracefully_when_reimport_fails(self, mock_run):
        """CalledProcessError on re-import → log warning, do not raise."""
        import subprocess
        mock_run.side_effect = [
            _mock_run(returncode=0, stdout=_SAMPLE_TASK_XML),
            subprocess.CalledProcessError(1, "schtasks", stderr="Access denied"),
        ]

        # Must not raise.
        w._apply_restart_on_failure()

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_tempfile_is_cleaned_up_on_success(self, mock_run, tmp_path):
        """The temporary XML file is removed after a successful re-import."""
        import tempfile
        created_paths: list[str] = []

        original_ntf = tempfile.NamedTemporaryFile

        def _tracking_ntf(**kwargs):
            handle = original_ntf(**kwargs)
            created_paths.append(handle.name)
            return handle

        mock_run.side_effect = [
            _mock_run(returncode=0, stdout=_SAMPLE_TASK_XML),
            _mock_run(returncode=0),
        ]

        with patch("paperprism_agent.winsvc.tempfile.NamedTemporaryFile", side_effect=_tracking_ntf):
            w._apply_restart_on_failure()

        import os
        for path in created_paths:
            assert not os.path.exists(path), f"Temp file {path} was not cleaned up"

    @patch("paperprism_agent.winsvc.subprocess.run")
    def test_create_task_calls_apply_restart_on_failure(self, mock_run, tmp_path: Path):
        """create_task() must call _apply_restart_on_failure after /Create."""
        # Calls: write_wrapper_bat (no subprocess), /Create, /Query /XML, /Create /XML /F
        mock_run.side_effect = [
            _mock_run(returncode=0),  # schtasks /Create base task
            _mock_run(returncode=0, stdout=_SAMPLE_TASK_XML),  # /Query /XML export
            _mock_run(returncode=0),  # /Create /XML /F re-import
        ]
        cfg = _make_cfg(tmp_path)
        w.create_task(cfg)

        all_arg_lists = [c.args[0] for c in mock_run.call_args_list]
        # Verify the re-import call is present (3rd call).
        assert any("/XML" in args and "/Create" in args for args in all_arg_lists[1:]), (
            "Expected a schtasks /Create /XML /F re-import call for restart-on-failure"
        )
