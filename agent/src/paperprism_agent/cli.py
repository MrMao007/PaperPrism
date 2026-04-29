"""Command-line entry point.

Subcommands:
  serve       Run the HTTP server in the foreground.
  install     Install a macOS launchd LaunchAgent that keeps the server
              running across logins and auto-restarts on crash.
  uninstall   Remove the LaunchAgent.
  status      Print launchd state for the service.
  restart     Force launchd to (re)start the service immediately.
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


def cmd_install(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print(
            "install is currently macOS-only (launchd). "
            "On Linux use systemd --user; on Windows use Task Scheduler.",
            file=sys.stderr,
        )
        return 2

    cfg = Config.from_args(
        host=args.host,
        port=args.port,
        token=args.token,
        home=args.home,
    )
    cfg.paths.ensure()

    try:
        plist = launchd_mod.write_plist(cfg)
    except RuntimeError as exc:
        # Typically raised when invoked via an ephemeral `uvx` environment.
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Wrote plist: {plist}")

    launchd_mod.bootstrap()
    print(
        f"Loaded service {launchd_mod.LABEL} into launchd (domain gui/{os.getuid()})."
    )
    print(
        f"Agent is starting on http://{cfg.host}:{cfg.port}. "
        "Check `paperprism-agent status` or `paperprism-agent logs --follow`."
    )
    return 0


def cmd_uninstall(_args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print("uninstall is currently macOS-only (launchd).", file=sys.stderr)
        return 2
    launchd_mod.bootout()
    removed = launchd_mod.remove_plist()
    if removed:
        print(f"Removed {launchd_mod.plist_path()}")
    else:
        print("No plist to remove; launchd service unloaded (if present).")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print("status is currently macOS-only (launchd).", file=sys.stderr)
        return 2
    code, text = launchd_mod.print_status()
    if code != 0:
        print(
            f"Service {launchd_mod.LABEL} is NOT loaded.\n"
            "Run `paperprism-agent install` to set it up."
        )
        return 1
    print(text)
    return 0


def cmd_restart(_args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print("restart is currently macOS-only (launchd).", file=sys.stderr)
        return 2
    if not launchd_mod.is_loaded():
        print(
            "Service is not loaded. Run `paperprism-agent install` first.",
            file=sys.stderr,
        )
        return 1
    launchd_mod.kickstart()
    print(f"Requested kickstart of {launchd_mod.LABEL}.")
    return 0


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
    cmd = ["tail", f"-n{args.n}"]
    if args.follow:
        cmd.append("-f")
    cmd.append(str(target))
    try:
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
