"""Command-line entry point.

Subcommands:
  serve       Run the HTTP server in the foreground (all platforms).
  install     Register an auto-start service and start it immediately.
                macOS  → launchd LaunchAgent (~/Library/LaunchAgents/)
                Linux  → systemd --user unit (~/.config/systemd/user/)
  uninstall   Stop and remove the auto-start service.
  status      Print service status (launchd on macOS, systemd on Linux).
  restart     Force the service to (re)start immediately.
  logs        Tail the Agent logs.
  version     Print the Agent version.

Usage:
  paperprism-agent serve --port 17321
  paperprism-agent install
  python -m paperprism_agent serve
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys

import uvicorn

from paperprism_agent import __version__
from paperprism_agent import launchd as launchd_mod
from paperprism_agent import systemd as systemd_mod
from paperprism_agent import winsvc as winsvc_mod
from paperprism_agent.config import Config
from paperprism_agent.logging_setup import setup_logging
from paperprism_agent.paths import clear_runtime, write_runtime
from paperprism_agent.server import create_app


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paperprism-agent",
        description="Local Agent for PaperPrism.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the HTTP server in the foreground.")
    serve.add_argument("--host", default=None, help="Bind host (default 127.0.0.1).")
    serve.add_argument("--port", type=int, default=None, help="Bind port (default 17321).")
    serve.add_argument("--token", default=None, help="Shared auth token. Empty = disabled.")
    serve.add_argument("--home", default=None, help="PAPERPRISM_HOME override.")
    serve.add_argument("--log-level", default="info", help="debug|info|warning|error")

    install = sub.add_parser(
        "install",
        help="Install launchd LaunchAgent (macOS) and start it.",
    )
    install.add_argument("--host", default=None)
    install.add_argument("--port", type=int, default=None)
    install.add_argument("--token", default=None)
    install.add_argument("--home", default=None)

    sub.add_parser("uninstall", help="Stop and remove the LaunchAgent.")
    sub.add_parser("status", help="Print launchctl state for the service.")
    sub.add_parser("restart", help="Force launchd to (re)start the service.")

    logs = sub.add_parser("logs", help="Tail Agent logs.")
    logs.add_argument(
        "--which",
        default="out",
        choices=["out", "err", "launchd-out", "launchd-err"],
        help="Which log to tail.",
    )
    logs.add_argument("-n", type=int, default=50, help="Initial line count.")
    logs.add_argument(
        "--follow",
        action="store_true",
        help="Follow the log (tail -f).",
    )

    sub.add_parser("version", help="Print the Agent version.")

    return p


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = Config.from_args(
        host=args.host,
        port=args.port,
        token=args.token,
        home=args.home,
    )
    cfg.paths.ensure()

    level = getattr(logging, args.log_level.upper(), logging.INFO)
    setup_logging(cfg.paths.logs, level=level)

    log = logging.getLogger("paperprism.cli")
    log.info(
        "Starting PaperPrism Agent v%s on http://%s:%d (home=%s)",
        __version__,
        cfg.host,
        cfg.port,
        cfg.paths.home,
    )

    write_runtime(
        cfg.paths,
        port=cfg.port,
        token=cfg.token,
        pid=os.getpid(),
        version=__version__,
    )

    # Ensure runtime.json is cleaned up on graceful shutdown.
    def _cleanup(signum, _frame):
        log.info("Received signal %s; shutting down", signum)
        clear_runtime(cfg.paths)
        # Let uvicorn handle the signal; we just flush state.

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    app = create_app(cfg)

    try:
        uvicorn.run(
            app,
            host=cfg.host,
            port=cfg.port,
            log_level=args.log_level,
            access_log=False,
            # Avoid uvicorn reconfiguring our logging.
            log_config=None,
        )
    finally:
        clear_runtime(cfg.paths)

    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def _unsupported_platform(command: str) -> int:
    print(
        f"`{command}` is not supported on {sys.platform}. "
        "Supported platforms: macOS (launchd), Linux (systemd --user), "
        "Windows (Task Scheduler via schtasks.exe).",
        file=sys.stderr,
    )
    return 2


def cmd_install(args: argparse.Namespace) -> int:
    cfg = Config.from_args(
        host=args.host,
        port=args.port,
        token=args.token,
        home=args.home,
    )
    cfg.paths.ensure()

    if sys.platform == "darwin":
        try:
            plist = launchd_mod.write_plist(cfg)
        except RuntimeError as exc:
            # Raised when invoked via an ephemeral `uvx` environment.
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Wrote plist: {plist}")
        launchd_mod.bootstrap()
        print(
            f"Loaded service {launchd_mod.LABEL} into launchd "
            f"(domain gui/{os.getuid()})."
        )

    elif sys.platform.startswith("linux"):
        try:
            unit = systemd_mod.write_unit(cfg)
        except RuntimeError as exc:
            # Raised when invoked via an ephemeral `uvx` environment.
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Wrote unit: {unit}")
        try:
            systemd_mod.enable_and_start()
        except subprocess.CalledProcessError as exc:
            print(f"systemctl failed: {exc.stderr or exc}", file=sys.stderr)
            return 1
        print(f"Enabled and started {systemd_mod.UNIT_NAME}.")
        print(systemd_mod.linger_hint())

    elif sys.platform == "win32":
        try:
            bat = winsvc_mod.write_wrapper_bat(cfg)
            winsvc_mod.create_task(cfg)
        except RuntimeError as exc:
            # Raised when invoked via an ephemeral `uvx` environment.
            print(str(exc), file=sys.stderr)
            return 2
        except subprocess.CalledProcessError as exc:
            print(f"schtasks failed: {exc.stderr or exc}", file=sys.stderr)
            return 1
        print(f"Wrote launcher: {bat}")
        print(f"Registered Task Scheduler task: {winsvc_mod.TASK_NAME}")
        try:
            winsvc_mod.run_task_now()
            print(f"Task {winsvc_mod.TASK_NAME} started.")
        except subprocess.CalledProcessError:
            print("Task registered but could not start immediately; it will run at next logon.")

    else:
        return _unsupported_platform("install")

    print(
        f"Agent is starting on http://{cfg.host}:{cfg.port}. "
        "Check `paperprism-agent status` or `paperprism-agent logs --follow`."
    )
    return 0


def cmd_uninstall(_args: argparse.Namespace) -> int:
    if sys.platform == "darwin":
        launchd_mod.bootout()
        removed = launchd_mod.remove_plist()
        if removed:
            print(f"Removed {launchd_mod.plist_path()}")
        else:
            print("No plist to remove; launchd service unloaded (if present).")
        return 0

    if sys.platform.startswith("linux"):
        systemd_mod.stop_and_disable()
        removed = systemd_mod.remove_unit()
        if removed:
            print(f"Removed {systemd_mod.unit_path()}")
        else:
            print("No unit file to remove; systemd service stopped (if present).")
        return 0

    if sys.platform == "win32":
        winsvc_mod.end_task()
        winsvc_mod.delete_task()
        bat = winsvc_mod._wrapper_bat_path(Config.from_args())
        if bat.exists():
            bat.unlink()
            print(f"Removed launcher: {bat}")
        print(f"Deleted Task Scheduler task: {winsvc_mod.TASK_NAME}")
        return 0

    return _unsupported_platform("uninstall")


def cmd_status(_args: argparse.Namespace) -> int:
    if sys.platform == "darwin":
        code, text = launchd_mod.print_status()
        if code != 0:
            print(
                f"Service {launchd_mod.LABEL} is NOT loaded.\n"
                "Run `paperprism-agent install` to set it up."
            )
            return 1
        print(text)
        return 0

    if sys.platform.startswith("linux"):
        code, text = systemd_mod.print_status()
        if code != 0:
            print(
                f"Service {systemd_mod.UNIT_NAME} is NOT active.\n"
                "Run `paperprism-agent install` to set it up."
            )
            return 1
        print(text)
        return 0

    if sys.platform == "win32":
        if not winsvc_mod.task_exists():
            print(
                f"Task {winsvc_mod.TASK_NAME} is NOT registered.\n"
                "Run `paperprism-agent install` to set it up."
            )
            return 1
        code, text = winsvc_mod.print_status()
        print(text)
        return code

    return _unsupported_platform("status")


def cmd_restart(_args: argparse.Namespace) -> int:
    if sys.platform == "darwin":
        if not launchd_mod.is_loaded():
            print(
                "Service is not loaded. Run `paperprism-agent install` first.",
                file=sys.stderr,
            )
            return 1
        launchd_mod.kickstart()
        print(f"Requested kickstart of {launchd_mod.LABEL}.")
        return 0

    if sys.platform.startswith("linux"):
        if not systemd_mod.is_active():
            print(
                "Service is not active. Run `paperprism-agent install` first.",
                file=sys.stderr,
            )
            return 1
        try:
            systemd_mod.restart()
        except subprocess.CalledProcessError as exc:
            print(f"systemctl restart failed: {exc.stderr or exc}", file=sys.stderr)
            return 1
        print(f"Restarted {systemd_mod.UNIT_NAME}.")
        return 0

    if sys.platform == "win32":
        if not winsvc_mod.task_exists():
            print(
                f"Task {winsvc_mod.TASK_NAME} is not registered. "
                "Run `paperprism-agent install` first.",
                file=sys.stderr,
            )
            return 1
        try:
            winsvc_mod.restart()
        except subprocess.CalledProcessError as exc:
            print(f"schtasks failed: {exc.stderr or exc}", file=sys.stderr)
            return 1
        print(f"Restarted Task Scheduler task: {winsvc_mod.TASK_NAME}.")
        return 0

    return _unsupported_platform("restart")


def cmd_logs(args: argparse.Namespace) -> int:
    cfg = Config.from_args()
    mapping = {
        "out": cfg.paths.logs / "agent.out.log",
        "err": cfg.paths.logs / "agent.err.log",
        "launchd-out": cfg.paths.logs / "launchd.out.log",
        "launchd-err": cfg.paths.logs / "launchd.err.log",
    }
    target = mapping[args.which]
    if not target.exists():
        print(f"No such log yet: {target}", file=sys.stderr)
        return 1
    try:
        if sys.platform == "win32":
            # Windows has no `tail`; use PowerShell's Get-Content instead.
            ps_cmd = f"Get-Content -Path '{target}' -Tail {args.n}"
            if args.follow:
                ps_cmd += " -Wait"
            return subprocess.call(["powershell", "-NoProfile", "-Command", ps_cmd])
        else:
            cmd = ["tail", f"-n{args.n}"]
            if args.follow:
                cmd.append("-f")
            cmd.append(str(target))
            return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


DISPATCH = {
    "serve": cmd_serve,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "status": cmd_status,
    "restart": cmd_restart,
    "logs": cmd_logs,
    "version": cmd_version,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = DISPATCH.get(args.command)
    if handler is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
