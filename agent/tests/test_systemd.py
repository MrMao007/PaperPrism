"""Unit tests for systemd.py — Linux systemd --user unit helpers.

These tests run on all platforms (macOS / Linux / Windows) because
they mock out subprocess calls and filesystem paths. No real systemctl
is invoked, so the suite stays CI-friendly on macOS runners.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from paperprism_agent import systemd as s
from paperprism_agent.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path) -> Config:
    cfg = Config.from_args(home=str(tmp_path))
    cfg.paths.ensure()
    return cfg


# ---------------------------------------------------------------------------
# unit_path
# ---------------------------------------------------------------------------

class TestUnitPath:
    def test_default_location_under_home(self):
        path = s.unit_path()
        assert path.name == s.UNIT_NAME
        assert "systemd" in str(path)
        assert "user" in str(path)

    def test_respects_xdg_config_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        custom = tmp_path / "custom_config"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(custom))
        path = s.unit_path()
        assert path == custom / "systemd" / "user" / s.UNIT_NAME


# ---------------------------------------------------------------------------
# resolve_launcher
# ---------------------------------------------------------------------------

class TestResolveLauncher:
    def test_returns_list_of_strings(self):
        result = s.resolve_launcher()
        assert isinstance(result, list)
        assert all(isinstance(item, str) for item in result)
        assert len(result) >= 2

    def test_frozen_binary_returns_sys_executable(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("sys.frozen", True, raising=False)
        import sys
        result = s.resolve_launcher()
        assert result == [sys.executable, "serve"]

    def test_uvx_cache_raises_runtime_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        uvx_cache = tmp_path / ".cache" / "uv" / "tools" / "bin" / "python"
        uvx_cache.parent.mkdir(parents=True)
        uvx_cache.touch()

        import sys
        monkeypatch.setattr(sys, "executable", str(uvx_cache))
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with pytest.raises(RuntimeError, match="ephemeral"):
            s.resolve_launcher()


# ---------------------------------------------------------------------------
# build_unit
# ---------------------------------------------------------------------------

class TestBuildUnit:
    def test_contains_exec_start(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        unit = s.build_unit(cfg)
        assert "ExecStart=" in unit

    def test_contains_paperprism_home(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        unit = s.build_unit(cfg)
        assert f"PAPERPRISM_HOME={tmp_path}" in unit

    def test_contains_host_and_port(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        unit = s.build_unit(cfg)
        assert "PAPERPRISM_HOST=127.0.0.1" in unit
        assert "PAPERPRISM_PORT=17321" in unit

    def test_token_included_when_set(self, tmp_path: Path):
        cfg = Config.from_args(home=str(tmp_path), token="supersecret")
        cfg.paths.ensure()
        unit = s.build_unit(cfg)
        assert "PAPERPRISM_TOKEN=supersecret" in unit

    def test_token_absent_when_empty(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        unit = s.build_unit(cfg)
        assert "PAPERPRISM_TOKEN" not in unit

    def test_systemd_sections_present(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        unit = s.build_unit(cfg)
        assert "[Unit]" in unit
        assert "[Service]" in unit
        assert "[Install]" in unit

    def test_restart_on_failure(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        unit = s.build_unit(cfg)
        assert "Restart=on-failure" in unit

    def test_log_paths_point_to_home_logs(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        unit = s.build_unit(cfg)
        assert str(tmp_path / "logs" / "agent.out.log") in unit
        assert str(tmp_path / "logs" / "agent.err.log") in unit

    def test_secrets_injected(self, tmp_path: Path):
        cfg = _make_cfg(tmp_path)
        secrets_file = cfg.paths.secrets_file
        secrets_file.write_text("OPENAI_API_KEY=sk-test123\n")
        secrets_file.chmod(0o600)
        unit = s.build_unit(cfg)
        assert "OPENAI_API_KEY=sk-test123" in unit


# ---------------------------------------------------------------------------
# write_unit
# ---------------------------------------------------------------------------

class TestWriteUnit:
    def test_creates_unit_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg = _make_cfg(tmp_path)
        unit_dir = tmp_path / "systemd" / "user"
        monkeypatch.setattr(s, "unit_path", lambda: unit_dir / s.UNIT_NAME)

        written = s.write_unit(cfg)
        assert written.exists()
        content = written.read_text()
        assert "[Unit]" in content

    def test_creates_parent_directories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg = _make_cfg(tmp_path)
        deep_path = tmp_path / "a" / "b" / "c" / s.UNIT_NAME
        monkeypatch.setattr(s, "unit_path", lambda: deep_path)

        s.write_unit(cfg)
        assert deep_path.exists()


# ---------------------------------------------------------------------------
# systemctl wrappers (all mock subprocess)
# ---------------------------------------------------------------------------

class TestSystemctlWrappers:
    """Verify the correct systemctl --user sub-commands are invoked."""

    def _mock_run(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        result = MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        return result

    @patch("paperprism_agent.systemd.subprocess.run")
    def test_daemon_reload(self, mock_run):
        mock_run.return_value = self._mock_run()
        s.daemon_reload()
        mock_run.assert_called_once_with(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("paperprism_agent.systemd.subprocess.run")
    def test_enable_and_start(self, mock_run):
        mock_run.return_value = self._mock_run()
        s.enable_and_start()
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["systemctl", "--user", "daemon-reload"] in calls
        assert ["systemctl", "--user", "enable", "--now", s.UNIT_NAME] in calls

    @patch("paperprism_agent.systemd.subprocess.run")
    def test_stop_and_disable(self, mock_run):
        mock_run.return_value = self._mock_run()
        s.stop_and_disable()
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["systemctl", "--user", "stop", s.UNIT_NAME] in calls
        assert ["systemctl", "--user", "disable", s.UNIT_NAME] in calls
        assert ["systemctl", "--user", "daemon-reload"] in calls

    @patch("paperprism_agent.systemd.subprocess.run")
    def test_is_active_true(self, mock_run):
        mock_run.return_value = self._mock_run(returncode=0)
        assert s.is_active() is True

    @patch("paperprism_agent.systemd.subprocess.run")
    def test_is_active_false(self, mock_run):
        mock_run.return_value = self._mock_run(returncode=3)
        assert s.is_active() is False

    @patch("paperprism_agent.systemd.subprocess.run")
    def test_print_status_ok(self, mock_run):
        mock_run.return_value = self._mock_run(returncode=0, stdout="active (running)")
        code, text = s.print_status()
        assert code == 0
        assert "active" in text

    @patch("paperprism_agent.systemd.subprocess.run")
    def test_print_status_inactive(self, mock_run):
        mock_run.return_value = self._mock_run(returncode=3, stderr="inactive")
        code, text = s.print_status()
        assert code == 3
        assert "inactive" in text

    @patch("paperprism_agent.systemd.subprocess.run")
    def test_restart(self, mock_run):
        mock_run.return_value = self._mock_run()
        s.restart()
        mock_run.assert_called_once_with(
            ["systemctl", "--user", "restart", s.UNIT_NAME],
            capture_output=True,
            text=True,
            check=True,
        )


# ---------------------------------------------------------------------------
# remove_unit
# ---------------------------------------------------------------------------

class TestRemoveUnit:
    def test_removes_existing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        target = tmp_path / s.UNIT_NAME
        target.write_text("[Unit]\n")
        monkeypatch.setattr(s, "unit_path", lambda: target)
        assert s.remove_unit() is True
        assert not target.exists()

    def test_returns_false_if_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        target = tmp_path / s.UNIT_NAME
        monkeypatch.setattr(s, "unit_path", lambda: target)
        assert s.remove_unit() is False


# ---------------------------------------------------------------------------
# linger_hint
# ---------------------------------------------------------------------------

class TestLingerHint:
    def test_contains_loginctl(self):
        hint = s.linger_hint()
        assert "loginctl enable-linger" in hint

    def test_contains_username_or_placeholder(self):
        hint = s.linger_hint()
        # Should contain either a real username or the $(whoami) fallback.
        assert len(hint) > 20
