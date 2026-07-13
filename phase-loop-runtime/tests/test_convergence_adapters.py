from pathlib import Path

from phase_loop_runtime.convergence.adapters import AdapterExecutionRequest, run_codex_adapter
from phase_loop_runtime.convergence.contracts import AdmissionRequest


def test_adapter_rejects_out_of_bounds_command(tmp_path: Path):
    admission = AdmissionRequest("a", 1, "f", "d", "head", "repo", "key")
    request = AdapterExecutionRequest("a", admission, ("not-codex",), tmp_path, 1, "execute")
    assert run_codex_adapter(request).status.value == "blocked"
