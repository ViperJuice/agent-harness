from .base import AdapterExecutionRequest, run_bounded
from phase_loop_runtime.train_ledger import ConvergenceResultEnvelope, ConvergenceResultStatus

def run_outside_agent_adapter(request: AdapterExecutionRequest, submission: dict | None = None) -> ConvergenceResultEnvelope:
    if submission is not None:
        from phase_loop_runtime.conformance import validate_outside_agent_submission
        if validate_outside_agent_submission(submission).status.value != "pass":
            return ConvergenceResultEnvelope(ConvergenceResultStatus.BLOCKED, request.attempt_id, "outside-agent submission failed conformance")
    return run_bounded(request, expected_prefix="outside-agent")
