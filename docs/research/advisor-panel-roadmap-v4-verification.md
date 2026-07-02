# Advisor-Panel Roadmap v4 Verification

Date: 2026-06-30
Last refreshed: 2026-07-01

This document is the metadata-only closeout evidence for
`specs/phase-plans-v4.md`.

## Runtime And Skill Verification

- Focused panel/launcher/routing/skill slice:
  `PYTHONPATH=src python -m pytest tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_governed_gate_crfixes.py tests/test_governed_review.py tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py tests/test_model_class_policy.py tests/test_route_log.py tests/test_phase_loop_launcher.py -q`
  passed with 82 tests, 52 skipped, and 299 subtests.
- Full runtime suite:
  `PYTHONPATH=src python -m pytest -q`
  passed with 1299 tests, 625 skipped, and 458 subtests.
- Roadmap validation:
  `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md`
  passed for 6 phases.
- Manifest validation:
  `python -m json.tool plans/manifest.json`
  passed.
- Agent-harness diff check:
  `git diff --check`
  passed.

## Staged Artifact Evidence

Structured smoke evidence is in
`phase-loop-runtime/tests/test_panel_invoker_spawn.py`:

- `test_codex_command_prompt_references_staged_artifact_file` proves the
  Codex leg receives a compact prompt that points to `review-bundle.md`
  rather than embedding the artifact body in argv or stdin.
- `test_gemini_command_prompt_references_staged_artifact_file_with_add_dir`
  proves the Gemini leg receives the same staged-file pointer prompt and does
  not depend on `--add-dir`.
- `test_claude_leg_uses_tui_sonnet5_max_effort_and_canonical_output_file`
  proves the Claude leg uses the Claude Code TUI with `claude-sonnet-5`, max
  effort, API-key env stripping, no `claude -p`, and deterministic
  `panel-claude.txt` file output.

No live model-output transcript was recorded in this closeout; the proof is
structured command-construction/status evidence from the runtime tests.

Metadata-only liveness observed all three CLI legs on PATH:
`available_panel_legs=codex,gemini,claude`.

## Dotfiles Cutover Evidence

Dotfiles redaction was executed in
`/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630` on branch
`codex/advisor-panel-redact-20260630`.

Checks passed:

- `bash -n bootstrap.sh`
- `git diff --check`
- `test ! -e shared/skills/advisor-panel/scripts/run_cli_panels.sh`
- `test ! -e shared/skills/advisor-panel/scripts/run_claude_leg.sh`
- `test ! -e shared/skills/advisor-panel/references/capability-matrix.md`
- inverted `rg -n "run_cli_panels|run_claude_leg" shared/skills/advisor-panel`
  returned no matches.

The remaining dotfiles advisor-panel path is
`shared/skills/advisor-panel/SKILL.md`, a compatibility shim pointing to the
agent-harness runtime and harness-prefixed advisor-panel skills.

## Issue Closeout Notes

Issue #36 can be updated with:

- Agent-harness owns `phase_loop_runtime.panel_invoker`.
- Panel statuses and timeout policy are frozen and tested.
- Codex and Gemini receive compact prompts that point to staged
  `review-instructions.md` and `review-bundle.md` files.
- Claude uses local Claude Code TUI with `claude-sonnet-5`, a `2.1.197`
  minimum-version gate, and canonical scratch `panel-claude.txt` output.
- Full runtime suite passed.

Issue #135 can be updated with:

- Dotfiles no longer carries standalone advisor-panel scripts or reference
  implementation files.
- The unprefixed dotfiles `advisor-panel` skill is compatibility guidance only.
- Bootstrap continues to install phase-loop workflow skills from the pinned
  agent-harness clone.
- Redaction checks and the agent-harness staged-artifact smoke tests passed.

No secrets, local auth payloads, provider transcripts, or environment values are
included in this evidence.
