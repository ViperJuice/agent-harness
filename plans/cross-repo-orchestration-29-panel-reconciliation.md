# Advisor-panel reconciliation — #29 cross-repo orchestration design

3 legs (native Claude Opus + repo access; Codex GPT-5.5; Gemini 3.1 Pro). **Unanimous PARTIALLY AGREE:** Option C's *topology* is right (a runtime coordinator composing the **unchanged** per-repo `run_loop`; deep runner integration and pure-skill both correctly rejected; coordinator-side durable ledger correct). But the **central primitive — IF-gate-as-cross-repo-edge / DAG-of-DAGs — is broken** and must be replaced, and several "composes for free" claims were overclaimed.

## Verified corrections (repo-grounded, all/most legs)
1. **The IF-gate cross-repo edge does not exist — even within a repo.** Tokens are hardwired `IF-0-<ALIAS>-<n>` where `<ALIAS>` = the producing phase's alias (`roadmap_lint.py:72`, `closeout_validation.py:9`); my example tokens (`IF-PORTAL-SCHEMA-v2`) are **invalid**. There is **no consume-side verification** (the DAG is alias `Depends on` + document order; gates are validated produce-side only) and **no content identity** (`reconcile.py` confirms the trailing `-n` is a sequence integer, not a hash). So a cross-repo gate is **net-new mechanism**, not a lift.
2. **`drift_audit` is read-only aggregation** — nothing reusable for orchestration. The "multi-repo already modeled" claim is true-but-vacuous.
3. **The governed pre-merge gate is physically single-repo** (inside closeout, `git diff --cached` of one worktree). Per-repo `--governed` composes; a cross-repo gate is net-new.
4. **`EXECUTE_MERGE_TARGET` does not exist** in the runtime — remove the citation (the deep-integration rejection still stands on substance).
5. **The #28 publish flow lives in the execute-*skill prose*, not the runtime** (Codex). A runtime-owned coordinator cannot reuse it as-is — PR creation / draft-readiness / linking / publication-blockers must be **factored into a runtime publishing primitive** first.

## The reframe (Claude, the single most valuable move): gate on the upstream MERGE-COMMIT SHA
never-commit-to-main means every upstream landing yields a merge SHA. Gate downstream on the upstream **merge** (not "gate produced"). This **dissolves the content-identity question** (the merge SHA *is* the shipped contract; downstream branches/verifies off it), and **kills a false-green bug** I'd designed in (a "produced+verified, PR still open" downstream-start trigger makes the downstream worktree test against an upstream artifact that isn't really there). All three legs agree cross-repo gates need a versioned identity pinned to the upstream commit SHA (Codex/Gemini add a content hash + consumer-verified hash as hardening).

## The key divergence — the cross-repo (train-level) governed gate — and how it settles
- **Claude:** merge-gated-*serial* (downstream PR opens only after upstream merges) kills false-green; a cross-repo gate is then structurally impossible (never a moment all PRs are open) → **defer it**; per-repo `--governed` only.
- **Codex + Gemini (2 of 3):** the train-level gate is **load-bearing, not optional** — a portal-only reviewer can approve a migration that's locally correct but **wrong for message-board**, and per-repo merging creates the **partial-merge rollback disaster** (A on `main`, B fails → manual revert nightmare). So hold **all** PRs as drafts → one **train-level governed review** → **sequential merge**; forward-only is safe *only because* nothing merges until global approval.

**Settlement (synthesis, not vote-count):** the rollback disaster is the more severe, more consequential risk for a *cross-repo* change, and it's *created* by pure merge-gated-serial — so the 2-of-3 structure wins: **hold linked draft PRs → train-level governed review → sequential merge.** Claude's false-green concern is preserved by **re-verifying each downstream node against the upstream's merged SHA before the downstream merges** (so even though downstream was built against the upstream branch, it's revalidated against the real merged contract). Claude's merge-SHA-as-identity is preserved (downstream pins/re-verifies against it). Codex's **expand/contract (backward-compatible) upstream contract** is the recommended style that makes even the sequential merges low-risk.

## Reconciled architecture — "thinner Option C"
- **KEEP:** the runtime **coordinator** + a **durable, append-only, resumable train ledger** (node status, branch, PR URL, **upstream merge SHA**, consumed-gate identity, merge order) — `.phase-loop/` stays per-repo. A thin `run-train` skill fronts it.
- **DROP:** the DAG-of-DAGs IF-gate engine. Edges = `Depends on: <upstream node>`; the cross-repo contract = a **versioned gate pinned to the upstream merge SHA** (new namespace + validator, not a lift).
- **FLOW (MVP):** run each per-repo `run_loop` (unchanged) to open **linked draft PRs** in dependency order → **train-level governed review** of the bundle → **sequential merge**, **re-verifying each downstream node against the upstream merged SHA** before it merges. Per-repo `--governed` within each loop. Recommend expand/contract upstream contracts.
- **PREREQUISITE:** factor the #28 worktree→branch→PR publish flow out of the skill prose into a **runtime publishing primitive** the coordinator (and the execute skills) share.
- **SCOPE:** serial, ordered, 2-3 repos. Defer arbitrary parallel DAG scheduling and the content-hash/consumer-verified-hash hardening.

## MVP slice (one line, reconciled)
A runtime release-train coordinator: serially runs the unchanged per-repo `run_loop` (per-repo `--governed`) to open **linked draft PRs** for 2-3 ordered repos, persists a durable train ledger (statuses, PR URLs, upstream merge SHAs, order), holds for **one train-level governed review**, then **merges sequentially — re-verifying each downstream against the upstream merged SHA** — built on a factored-out runtime publishing primitive. Defer the IF-gate-DAG engine and arbitrary parallel scheduling.

## Top decisions for the roadmap
1. Replace the IF-gate edge with **merge-SHA-pinned cross-repo gates** + `Depends on: <upstream node>` (new schema/validator).
2. **Factor publishing into a runtime primitive** (prerequisite phase) — the coordinator can't depend on skill-prose publish.
3. **Train-level governed review is MVP** (hold drafts → review → sequential merge) **+ downstream re-verify against the upstream merge SHA**; expand/contract recommended.
4. Durable coordinator-side **train ledger**; correct the overclaimed framing (no consume edge / no identity / drift_audit read-only / remove `EXECUTE_MERGE_TARGET`).
