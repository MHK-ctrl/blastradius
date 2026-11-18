from __future__ import annotations

import json

import pytest
from conftest import make_row

from blastradius.cli import main
from blastradius.report import REQUIRED_KEYS
from blastradius.storage import SqliteStore


def test_help_exits_zero_and_lists_commands(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    for command in ("report", "verify", "dryrun", "index"):
        assert command in output


def test_report_json_has_the_required_shape(tmp_path, capsys):
    db = tmp_path / "journal.db"
    store = SqliteStore(db)
    store.insert(make_row(intent_hash="h1", latency_ms=2.0))
    store.close()

    assert main(["report", "--db", str(db), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    for key in REQUIRED_KEYS:
        assert key in data
    assert data["effects"] == 1


def test_strict_exits_one_on_a_duplicate_write(tmp_path):
    db = tmp_path / "journal.db"
    store = SqliteStore(db)
    store.insert(make_row(intent_hash="h1"))
    store.insert(make_row(intent_hash="h1", attempt=2))
    store.close()
    assert main(["report", "--db", str(db), "--strict"]) == 1


def test_strict_exits_zero_on_a_clean_journal(tmp_path):
    db = tmp_path / "journal.db"
    store = SqliteStore(db)
    store.insert(make_row(intent_hash="h1"))
    store.close()
    assert main(["report", "--db", str(db), "--strict"]) == 0


def test_verify_lists_pending_rows_and_exits_one(tmp_path, capsys):
    db = tmp_path / "journal.db"
    store = SqliteStore(db)
    store.insert(make_row(intent_hash="h1", state="pending"))
    store.close()
    assert main(["verify", "--db", str(db)]) == 1
    assert "pending" in capsys.readouterr().out


def test_verify_exits_zero_when_nothing_is_pending(tmp_path, capsys):
    db = tmp_path / "journal.db"
    store = SqliteStore(db)
    store.insert(make_row(intent_hash="h1"))
    store.close()
    assert main(["verify", "--db", str(db)]) == 0
    assert "no pending effects" in capsys.readouterr().out


def test_verify_json_is_parseable(tmp_path, capsys):
    db = tmp_path / "journal.db"
    store = SqliteStore(db)
    store.insert(make_row(intent_hash="h1", state="pending"))
    store.close()
    main(["verify", "--db", str(db), "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["effect_name"] == "refund"
    assert len(rows) == 1


def test_dryrun_without_a_command_exits_two(tmp_path):
    assert main(["dryrun", "--db", str(tmp_path / "j.db")]) == 2
