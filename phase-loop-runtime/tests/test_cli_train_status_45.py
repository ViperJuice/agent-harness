"""#45 — `phase-loop train-status`: non-mutating cross-repo train ledger inspection."""

from __future__ import annotations

import json
from pathlib import Path

from phase_loop_runtime.cli import main as cli_main
from phase_loop_runtime.train_ledger import LedgerRecord, append_record, default_ledger_path

TRAIN_MD = """# Release Train: t

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** submodule path=vendor/repo-a
"""


def _setup(tmp: Path) -> Path:
    train = tmp / "train.md"
    train.write_text(TRAIN_MD, encoding="utf-8")
    ledger = default_ledger_path(train.parent / ".train-ledger", train.stem)
    append_record(ledger, LedgerRecord(
        node_id="repo-a/specs/plan-a.md", status="merged", branch="b-a",
        pr_url="https://gh/a/1", upstream_merge_sha="sha-M-a", merge_order=0,
    ))
    append_record(ledger, LedgerRecord(
        node_id="repo-b/specs/plan-b.md", status="pr_open", branch="b-b",
        pr_url="https://gh/b/1", head_sha="sha-D-b", merge_order=1,
    ))
    return train


def test_train_status_human_output(tmp_path, capsys):
    train = _setup(tmp_path)
    rc = cli_main(["train-status", "--train", str(train)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[merged] repo-a/specs/plan-a.md" in out
    assert "[pr_open] repo-b/specs/plan-b.md" in out
    assert "sha-M-a" in out  # merged SHA surfaced


def test_train_status_json_output(tmp_path, capsys):
    train = _setup(tmp_path)
    rc = cli_main(["train-status", "--train", str(train), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    nodes = {n["node_id"]: n for n in payload["nodes"]}
    assert nodes["repo-a/specs/plan-a.md"]["status"] == "merged"
    assert nodes["repo-a/specs/plan-a.md"]["merged_sha"] == "sha-M-a"
    assert nodes["repo-b/specs/plan-b.md"]["status"] == "pr_open"
    # topo order: upstream before downstream
    assert [n["node_id"] for n in payload["nodes"]] == [
        "repo-a/specs/plan-a.md", "repo-b/specs/plan-b.md"
    ]


def test_train_status_no_ledger_lists_pending(tmp_path, capsys):
    train = tmp_path / "train.md"
    train.write_text(TRAIN_MD, encoding="utf-8")
    rc = cli_main(["train-status", "--train", str(train), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["nodes"] and all(n["status"] == "pending" for n in payload["nodes"])


def test_train_status_is_non_mutating(tmp_path):
    train = _setup(tmp_path)
    ledger = default_ledger_path(train.parent / ".train-ledger", train.stem)
    before = ledger.read_bytes()
    cli_main(["train-status", "--train", str(train)])
    assert ledger.read_bytes() == before, "train-status must not modify the ledger"


def test_train_status_missing_train_file(tmp_path, capsys):
    rc = cli_main(["train-status", "--train", str(tmp_path / "nope.md")])
    assert rc == 1
    assert "not found" in capsys.readouterr().err
