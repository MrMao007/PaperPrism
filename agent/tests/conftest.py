"""Pytest fixtures for the PaperPrism Agent test suite.

Each test gets an isolated ``PAPERPRISM_HOME`` under ``tmp_path``, so:

- no test ever touches the real ``~/.paperprism``;
- migrations apply on a fresh SQLite file every time;
- the module-level connection singleton in ``paperprism_agent.db`` is
  reset on entry and exit, so prior-test state can never leak in.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from paperprism_agent import db
from paperprism_agent.config import Config


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    """Isolated ``PAPERPRISM_HOME`` per test.

    Yields a ready-to-use :class:`Config` whose ``paths.home`` points at
    the per-test ``tmp_path``. The DB module singleton is closed before
    and after the test so each test sees a fresh connection.
    """
    monkeypatch.setenv("PAPERPRISM_HOME", str(tmp_path))
    db.close()
    cfg = Config.from_args(home=str(tmp_path))
    cfg.paths.ensure()
    try:
        yield cfg
    finally:
        db.close()


@pytest.fixture
def db_conn(tmp_home: Config) -> sqlite3.Connection:
    """Open the SQLite connection for the isolated home (auto-migrates)."""
    return db.connect(tmp_home.paths.db_file)
