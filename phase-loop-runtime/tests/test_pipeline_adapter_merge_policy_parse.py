from __future__ import annotations

import pytest

from phase_loop_runtime.pipeline_adapter.merge_policy import MergePolicy, MergePolicyParseError, parse


def test_missing_policy_defaults_to_required_without_approvers():
    assert parse({}) == MergePolicy(on_pass="required", approvers=())


def test_auto_without_approvers_is_valid():
    assert parse({"merge_policy": {"on_pass": "auto"}}) == MergePolicy(on_pass="auto", approvers=())


def test_never_without_approvers_is_valid():
    assert parse({"merge_policy": {"on_pass": "never"}}) == MergePolicy(on_pass="never", approvers=())


def test_required_with_approvers_is_valid():
    assert parse({"merge_policy": {"on_pass": "required", "approvers": ["ops", "release"]}}) == MergePolicy(
        on_pass="required",
        approvers=("ops", "release"),
    )


def test_explicit_required_without_approvers_raises():
    with pytest.raises(MergePolicyParseError):
        parse({"merge_policy": {"on_pass": "required"}})


def test_invalid_on_pass_raises():
    with pytest.raises(MergePolicyParseError):
        parse({"merge_policy": {"on_pass": "sometimes"}})


def test_json_policy_normalizes_approver_tuple():
    policy = parse({"merge_policy": '{"on_pass": "required", "approvers": "ops, release"}'})

    assert policy.approvers == ("ops", "release")
    assert policy.to_json() == {"on_pass": "required", "approvers": ["ops", "release"]}
