client<llm> PhaseLoopCloseoutClient {
  provider "openai-generic"
  options {
    base_url "https://example.invalid/v1"
    model "phase-loop-closeout"
    default_role "user"
  }
}

// terminal_status enum literals:
// unplanned, planned, executing, executed, awaiting_phase_closeout, complete, blocked, unknown
// verification_status enum literals:
// not_run, passed, failed, blocked
// blocker_class enum literals:
// missing_secret, account_or_billing_setup, admin_approval, destructive_operation,
// ambiguous_roadmap_selection, product_decision_missing, dirty_worktree_conflict,
// branch_sync_conflict, stalled_child_observation, repeated_verification_failure,
// sandbox_command_restriction, upstream_phase_unmet, contract_bug,
// gold_record_amendment, unretryable_external_outage, stuck_loop, none

class PhaseLoopCloseoutV1 {
  terminal_status string
  verification_status string
  dirty_paths string[]
  produced_if_gates string[]
  next_action string?
  blocker_class string?
  blocker_summary string?
  human_required bool?
  required_human_inputs string[]
}

function EmitPhaseCloseout(
  phase_alias: string,
  plan_produces: string[],
  plan_owned_files: string[],
  closeout_commit_sha: string?
) -> PhaseLoopCloseoutV1 {
  client PhaseLoopCloseoutClient
  prompt #"
Emit the phase-loop closeout for phase {{ phase_alias }} as one JSON object.

Required contract:
- Field-anchored enum list for terminal_status: unplanned, planned, executing, executed, awaiting_phase_closeout, complete, blocked, unknown.
- Field-anchored enum list for verification_status: not_run, passed, failed, blocked.
- terminal_status must never use dry_run. dry_run is an event-level execution mode, not a phase closeout status.
- dirty_paths must list repo-relative dirty paths left after execution.
- produced_if_gates must list the interface-freeze gates actually produced by this closeout.
- Completed closeouts must include every IF gate produced by the active plan and must not leave produced_if_gates empty.
- blocker_class must be null or one frozen blocker class; use none only when a non-null no-blocker marker is required.
- blocker_summary must be non-secret and null when there is no blocker.
- human_required must be true only for a true human decision or access blocker, false for repairable automation blockers, and null when not blocked.
- required_human_inputs must contain only non-secret inputs needed from a human.

Terminal-status decision tree:
- Use complete only when the phase work met acceptance, required verification passed, and every active-plan IF gate is listed in produced_if_gates.
- Use blocked when work cannot complete in this turn because verification, ownership, access, policy, or contract checks failed.
- Use executed when implementation ran but final acceptance, artifact tracking, or verification remains unresolved.
- Use awaiting_phase_closeout when verified phase-owned dirty output still needs the runner closeout mode to commit, isolate, or preserve it.
- Use planned or unplanned only for planning-state closeouts where execution did not run.
- Use executing only for an in-progress closeout and unknown only when the executor cannot determine the phase state.

Field-pair invariants:
- terminal_status=complete requires verification_status=passed.
- terminal_status=planned or terminal_status=unplanned requires verification_status=not_run.
- terminal_status=blocked requires verification_status=failed or verification_status=blocked.
- terminal_status=executed, terminal_status=executing, terminal_status=awaiting_phase_closeout, or terminal_status=unknown must use the most accurate verification_status from the work performed.

Active plan IF gates:
{% for gate in plan_produces %}
- {{ gate }}
{% endfor %}

Active plan owned files:
{% for owned_file in plan_owned_files %}
- {{ owned_file }}
{% endfor %}

Closeout commit SHA: {{ closeout_commit_sha | default("none") }}

{{ ctx.output_format }}
"#
}
