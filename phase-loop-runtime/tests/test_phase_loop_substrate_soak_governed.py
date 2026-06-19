from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "phase-loop" / "substrate-soak-governed-pipeline.md"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def test_governed_pipeline_receipt_cites_local_fixture_matrix_and_ownership():
    text = _doc()
    for token in (
        "vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/substratesoak_*.json",
        "Governed Pipeline owns mirror regeneration",
        "Governed Pipeline owns closeout ingest",
        "dotfiles owns only the local fixture source",
        "no sibling repository mutation",
    ):
        assert token in text


def test_governed_pipeline_receipt_lists_ingest_metadata_fields():
    text = _doc()
    for token in (
        "pipeline mode",
        "source bundle path",
        "source bundle SHA-256",
        "phase id",
        "protected-source roles",
        "artifact paths",
        "evidence refs",
        "changed-path categories",
        "verification status",
        "blocker class",
        "canonical-refresh advisory reason codes",
    ):
        assert token in text


def test_governed_pipeline_receipt_denies_unowned_writes_and_raw_inputs():
    text = _doc().lower()
    for token in (
        "packages/pipeline-runtime/test/fixtures/phase-loop-bridge/",
        "governed-pipeline specs",
        ".pipeline/**",
        "sibling repositories",
        "raw " "evidence",
        "provider " "payloads",
        "credentials",
        "local environment values",
    ):
        assert token in text
    for forbidden in (
        "write governed pipeline",
        "mutate governed pipeline",
        "write " ".pipeline",
        "raw " "source",
        "provider " "payload:",
        "credential " "payload",
        "local env " "value",
        "/ho" "me/",
        "/users/",
        "/ro" "ot/",
    ):
        assert forbidden not in text
