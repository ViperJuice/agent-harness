from pathlib import Path


def test_no_copied_canonical_outside_agent_schema_or_vectors():
    repo = Path(__file__).resolve().parents[2]
    forbidden_roots = [
        repo / "src" / "phase_loop_runtime",
        repo / "tests" / "fixtures" / "outside_agent_contract_drift",
    ]

    copied = []
    for root in forbidden_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix == ".json":
                text = path.read_text(encoding="utf-8")
                if "outside_agent_submission.v0.1" in text or "outside_agent_vector_manifest.v0.1" in text:
                    copied.append(path.relative_to(repo).as_posix())

    assert copied == []
