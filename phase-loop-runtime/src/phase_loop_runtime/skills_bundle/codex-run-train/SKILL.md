---
name: codex-run-train
description: "Harness entry point for the cross-repo release-train coordinator. Use when the user wants to run, resume, or inspect a multi-repo train roadmap: draft PRs across all nodes in topo order, gather train-level review, then merge sequentially with downstream re-verification."
---

# Harness Run Train

Thin bridge for the `phase-loop run-train` coordinator. All preflight,
draft-PR sequencing, train-level review, sequential merge, and downstream
re-verify logic lives in the runtime; this skill is the human entry point only.
Do NOT re-implement or contradict runtime guarantees here.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- Use the repo-local CLI: `phase-loop run-train --train <train-roadmap-file>`.
- Pass `--governed` to enable the merge phase (train-level review + sequential
  merge + downstream re-verify). Omitting `--governed` stops at `drafts_open`.
- Do NOT invoke `phase-loop run` on the train roadmap file — that is the
  per-repo loop and will not orchestrate a multi-repo train.
- Do NOT merge, force-push, or close PRs outside the coordinator; the runtime
  enforces the partial-merge and false-green guards.
- Inspect `phase-loop train-status --train <file>` to check the ledger without
  modifying state.

## Inputs

- Train roadmap path: a Markdown file with `## Nodes` listing
  `### Node: <repo> / <plan>` entries with `**Depends on:**` and
  `**Channel:**` fields.
- Optional `--governed` flag: activates train-level review → sequential merge →
  downstream re-verify.
- Optional `--ledger <path>`: explicit ledger path for crash-resume.

## Workflow

1. Resolve the train roadmap path (explicit arg or the user-supplied path).
2. Run preflight:
   `phase-loop run-train --train <file> --dry-run` (or inspect logs for
   preflight errors before the first real run).
3. Open draft PRs across all nodes in topo order:
   `phase-loop run-train --train <file>`
   The coordinator runs each repo's `run_loop` in series; a preflight failure
   stops before any PR is opened.
4. After all draft PRs are open (`status=drafts_open`), gather review:
   `phase-loop run-train --train <file> --governed`
   The train-level panel reviews the full set of draft changes.
5. On approval, the coordinator merges upstream nodes first, then re-verifies
   each downstream node against the upstream MERGED SHA before merging it.
   A re-verify failure halts the merge at that node; upstream merges are
   forward-only (never reverted).
6. Inspect the outcome: `phase-loop train-status --train <file>`.

## Failure Diagnostics

- `preflight_failed`: one or more nodes failed preflight checks; zero PRs were
  opened. Fix the reported issues and re-run.
- `drafts_open`: draft PRs opened; merge phase not yet run. Pass `--governed`
  to continue to review and merge.
- `review_halted`: the train-level panel did not approve; `terminal_blocker`
  carries `human_required=False` (the block is a non-human review terminal).
  No nodes were merged. Re-run after addressing review findings.
- `merge_halted`: upstream node(s) merged but a downstream re-verify failed;
  the failed node and all its dependents are blocked. The forward-only guard
  means already-merged nodes stay merged. Fix the integration issue and resume.
- `merge_failed`: a merge call returned an error (e.g. conflict, branch
  protection). The ledger records the failed node as `blocked`. Fix and resume.

## Resume

The coordinator is crash-resumable. If a run is interrupted, re-invoke the
same command with `--governed`; the ledger state drives which nodes are skipped
(already merged), re-verified (upstream merged but downstream not yet merged),
or retried (blocked).
