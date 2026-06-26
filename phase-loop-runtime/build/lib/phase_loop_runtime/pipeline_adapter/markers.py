from __future__ import annotations

import os
from pathlib import Path


def detect_pipeline_mode(repo_root: Path) -> bool:
    root = Path(repo_root)
    return (
        (root / ".pipeline").exists()
        or (root / ".github" / "workflows" / "pipeline-bootstrap.yml").exists()
        or os.environ.get("PHASE_LOOP_PIPELINE_MODE") == "true"
    )
