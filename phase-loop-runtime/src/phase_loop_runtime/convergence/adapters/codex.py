from .base import AdapterExecutionRequest, run_bounded
from phase_loop_runtime.train_ledger import ConvergenceResultEnvelope

def run_codex_adapter(request: AdapterExecutionRequest) -> ConvergenceResultEnvelope:
    return run_bounded(request, expected_prefix="codex")
