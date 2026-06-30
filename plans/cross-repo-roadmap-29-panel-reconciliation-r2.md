# Roadmap panel reconciliation (r2) — #29 cross-repo release-train

Roadmap `specs/phase-plans-cross-repo-v1.md` reviewed by the panel. Native Claude (repo-verified): **PARTIALLY AGREE** (block-class but patchable). Gemini: **DISAGREE** (fatal flaw). Codex: failed transiently (empty) — per the operator's "go with what's available" guidance, reconciled on the two repo-grounded legs, which **converge decisively** on the central finding.

## The block-class finding (both legs, repo-verified)
The roadmap's load-bearing safety invariant — "downstream re-verify against the upstream merged SHA" — inherited the reconciliation's *"rebase the downstream branch onto the upstream merged commit,"* which is **category-wrong / physically impossible**: two repos have unrelated git histories. The real mechanism is **re-resolving a consumption channel** (package/version pin | git submodule | workspace path) to the upstream ref. Gemini sharpened it to the deeper gap: since `run_loop` is **unchanged**, the coordinator has **no defined mechanism to inject the cross-repo dependency** into the downstream workspace at all — so the build (P3) and the false-green guard (P4) were both hollow/mock-only.

## Fixes folded into the roadmap (r2)
1. **New frozen contract IF-0-P2-2 — cross-repo consumption channel + injection primitive.** A per-edge channel descriptor (pin/submodule/workspace) + a runtime `set_upstream_ref(workspace, channel, ref)` the **coordinator** runs to point the channel at a given upstream ref **before invoking the unchanged `run_loop`**. P2 now produces IF-0-P2-1 **and** IF-0-P2-2 (3 lanes).
2. **P3** — inject the upstream **draft** branch/`head_sha` via `set_upstream_ref` before each downstream `run_loop` (so it builds against the real change-in-flight); plus a **train-level preflight entry-gate** (all repos clean + `gh` auth + remotes + base branches, before ANY PR — closes the partial-draft-train hole both legs flagged).
3. **P4** — re-verify rewritten: `set_upstream_ref(..., <upstream_merge_sha>)` then re-run verification (NOT a rebase); the P4 test + P5 CI invariant **assert the re-resolution to the merged SHA actually occurred**, not just that a function was called.
4. **IF-0-P1-1** returns `{branch, head_sha, pr_url, status}` (the coordinator needs the SHA to inject); reuse `git_topology.resolve_closeout_push_target`/`_gh_pr_metadata`.
5. **P2 ledger durability made self-consistent** (Claude): atomic append + a **tolerant resume reader that drops a malformed trailing line** — flagged as **net-new**, NOT a mirror of `events.py:read_events` (which crashes on a truncated line); "temp-rename" removed.
6. **Key files** corrected to the real reuse points: P4 `branch_ops.py`/`merge_policy.py` (visible as intra-repo, insufficient → channel, not rebase); the new `cross_repo_channel.py`.
7. Context + Assumption 6 added: the cross-repo dependency is a consumption channel, not a git relationship; a channel-less edge fails loud at `validate-roadmap` (P2).

`validate-roadmap` clean (5 phases). Verdict resolution: Gemini's "fatal as written" + Claude's "patchable in the roadmap" reconcile to: not-ready-as-was → **now patched**, with the injection primitive (IF-0-P2-2) as the load-bearing addition.
