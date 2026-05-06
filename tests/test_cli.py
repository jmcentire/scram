"""CLI argparse tests — exercises the parser without launching subprocesses."""

from __future__ import annotations

import pytest

from scram import cli


def test_parser_help_shows_all_subcommands() -> None:
    parser = cli._build_parser()
    # SystemExit raised by argparse when --help is passed; we use the
    # parser API directly to enumerate commands instead.
    actions = parser._subparsers._group_actions  # type: ignore[attr-defined]
    assert actions, "subparsers expected"
    sub_action = actions[0]
    choices = set(sub_action.choices)
    assert {"run", "fire", "list", "migrate"} <= choices


def test_parser_run_defaults_port_8400() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["run"])
    assert args.command == "run"
    assert args.port == 8400


def test_parser_fire_requires_reason_and_operator() -> None:
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["fire", "c-id"])  # missing required flags


def test_parser_fire_parses_all_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "fire",
            "c-id",
            "--reason",
            "drill",
            "--operator",
            "op-a",
            "--base-url",
            "http://example.test:8400",
        ]
    )
    assert args.command == "fire"
    assert args.condition_id == "c-id"
    assert args.reason == "drill"
    assert args.operator == "op-a"
    assert args.base_url == "http://example.test:8400"


def test_main_no_args_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    """argparse's required=True on subcommand makes empty argv raise SystemExit(2)."""
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_require_dsn_exits_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        cli._require_dsn()
    assert exc.value.code == 2


def test_require_dsn_returns_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://localhost/test")
    assert cli._require_dsn() == "postgres://localhost/test"
