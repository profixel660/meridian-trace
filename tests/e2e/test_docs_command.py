"""Tests for the ``meridian docs`` convenience command.

The ``docs`` command opens (or prints) the URL of a documentation page on
GitHub. Tests must NOT actually open a browser — we monkeypatch
``webbrowser.open`` and assert the right URL was passed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from meridian.cli import _DOCS_BASE_URL, _DOCS_TOPICS, _RELEASES_URL
from meridian.cli import app as cli_app

runner = CliRunner()


def test_docs_no_topic_opens_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """`meridian docs` (no topic) opens the docs index URL."""
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)
    result = runner.invoke(cli_app, ["docs"])
    assert result.exit_code == 0, result.stdout
    assert opened == [_DOCS_BASE_URL]


def test_docs_topic_opens_specific_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """`meridian docs install` opens the install page URL."""
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u) or True)

    result = runner.invoke(cli_app, ["docs", "install"])
    assert result.exit_code == 0, result.stdout
    assert opened == [f"{_DOCS_BASE_URL}/INSTALL.md"]


def test_docs_unknown_topic_errors_with_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown topic exits 2 and points at --list."""
    monkeypatch.setattr("webbrowser.open", lambda _u: True)
    result = runner.invoke(cli_app, ["docs", "totally-not-a-topic"])
    assert result.exit_code == 2
    assert "--list" in result.stdout


def test_docs_list_prints_every_topic() -> None:
    """`meridian docs --list` prints every shortcut + the base URL."""
    result = runner.invoke(cli_app, ["docs", "--list"])
    assert result.exit_code == 0
    for shortcut in _DOCS_TOPICS:
        assert shortcut in result.stdout
    assert _DOCS_BASE_URL in result.stdout


def test_docs_print_only_does_not_open_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--print` prints the URL and never calls webbrowser.open."""
    open_called: list[bool] = []

    def fake_open(_u: str) -> bool:
        open_called.append(True)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)
    result = runner.invoke(cli_app, ["docs", "concepts", "--print"])
    assert result.exit_code == 0
    assert f"{_DOCS_BASE_URL}/concepts.md" in result.stdout
    assert open_called == [], "--print must skip the browser open"


def test_root_help_epilog_mentions_docs_and_releases(tmp_projects_dir: Path) -> None:
    """`meridian --help` epilog points at both docs and releases URLs.

    Lowest-friction discoverability path for a new user — this is the first
    thing they see after install.
    """
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert _DOCS_BASE_URL in result.stdout
    assert _RELEASES_URL in result.stdout
    assert "meridian docs" in result.stdout
