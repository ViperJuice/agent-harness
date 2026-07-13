from phase_loop_runtime.cli import build_parser


def test_run_train_entrypoint_keeps_event_log_status_read_only_contract():
    args = build_parser().parse_args(["train-status", "--event-log", "coordinator.events.jsonl"])
    assert args.command == "train-status"
    assert args.event_log == "coordinator.events.jsonl"
