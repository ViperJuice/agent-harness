# Repo Validation Contract

A harness-neutral, local-first validation contract that gives coding agents one
command surface to run **before** they open a PR — locally, in a worktree, and in
CI — while GitHub stays the authoritative review and merge gate.

The goal is CI parity and faster agent iteration, **not** replacing GitHub
Actions and **not** requiring Dagger Cloud.

agent-harness owns this contract as a *neutral installable capability*: the same
`phase-loop`/`codex-phase-loop` runtime that drives every repo (this one,
governed-pipeline, consiliency-portal, regenesis, …) can resolve and run a
repo's validation contract without knowing anything repo-specific. The runtime
ships a resolver (`phase-loop repo-validate <target>`); consuming repos own their
actual checks (and any Dagger module). See
[`../phase-loop-runtime/src/phase_loop_runtime/repo_validation.py`](../phase-loop-runtime/src/phase_loop_runtime/repo_validation.py).

## Command Contract

A migrated repo exposes these six targets. Each maps to a validation *tier*:

| Target | Tier | Meaning |
|---|---:|---|
| `fast` | 0 | Formatting, lint/static checks, targeted unit tests, dependency sanity. No network unless the repo explicitly requires it. |
| `gate` | 1 | Pre-PR gate, ideally through Dagger: affected tests, contract/seam tests, local integration where practical, and the repo build. |
| `full` | 2 | Full local suite where feasible; expensive matrix/GPU/self-hosted checks may report a clear local skip and run in CI/nightly. |
| `fix` | 0 | Safe formatters and safe autofix tools only. |
| `affected` | 0/1 | Changed-file / workspace-aware affected-test selection; fall back to `gate` when the mapping is uncertain. |
| `doctor` | n/a | Environment and prerequisite report: tools, Docker/engine, Dagger, package manager, declared contracts, worktree assumptions. Always exits 0. |

`check` is accepted as an alias for `doctor`.

## Discovery

The resolver runs **only** a repo's *explicit* contract, in this order:

1. **`just agent::<target>`** — when a root `Justfile`/`justfile` declares
   `mod agent` (or `mod? agent`) **and** the repo ships an `agent.just` module
   (also probed at `agent/mod.just`, `agent/Justfile`, `agent/justfile`,
   `agent/.justfile`) that declares a recipe named `<target>`.
2. **`package.json` script `agent:<target>`** — an explicit npm/pnpm/yarn/bun
   script. The runner is chosen from a present lockfile
   (`pnpm-lock.yaml`→pnpm, `package-lock.json`→npm, `yarn.lock`→yarn,
   `bun.lock`/`bun.lockb`→bun); with no lockfile, the first available manager in
   `pnpm, npm, yarn, bun` order is used.

When both are declared and `just` is on `PATH`, the just contract wins. When a
just contract is declared but `just` is not installed, the resolver falls back to
an equivalent `package.json` script if one exists (emitting a warning).

**Fail closed.** An unmigrated repo returns exit **20**. The resolver never
guesses that `npm test`, `pytest`, `make test`, `go test`, or any native command
is the gate — agents must not invent CI.

The repo root is resolved with `git rev-parse --show-toplevel`, so the resolver
works from subdirectories and from linked git worktrees.

## Exit Codes

The frozen exit-code contract (identical to the dotfiles `agent-validation`
wrapper, so the local and neutral surfaces never drift):

| Code | Meaning |
|---:|---|
| 0 | Validation or doctor completed. |
| 2 | Usage error (unknown target). |
| 10 | Not inside a git work tree. |
| 20 | No explicit agent validation contract in the repo (fail closed). |
| 21 | A contract exists, but its runner is missing (e.g. `just` / the pinned package manager not on `PATH`). |
| 30 | The repo-local validation command ran and failed. |

## Tier Model

- **Tier 0** (`fast`, `fix`) is the agent inner loop: aim for under ~60s on
  ordinary edits, and require **no secrets**.
- **Tier 1** (`gate`, often `affected`) is the pre-PR gate, usually 2–10 minutes.
  It is the command an agent runs before opening or updating a PR. For medium and
  large repos, `gate` should call Dagger or a repo-local wrapper around Dagger.
- **Tier 2** (`full`) plus the CI matrix remain the authoritative merge gate:
  full suite, dependency/security scan, deployment dry-run, and tests too slow or
  hardware-specific for every local run. GitHub Actions remains the required-check
  surface unless a repo deliberately chooses another merge authority.

## Dagger Posture (optional)

Dagger is **optional** and, when used, repo-owned. Use the open-source Dagger
Engine and local/self-hosted compute first; **Dagger Cloud is not required** by
this standard. A pilot repo keeps Dagger thin:

- `dagger.json` at the repo root and a `dagger/` module in the repo.
- Functions mirroring the contract: `fast`, `gate`, `full`, `affected`, plus the
  building blocks (`build`, `test`, `lint`, `typecheck`, `contracts`, `seams`,
  `security`).
- Pinned language/runtime containers; understandable package-manager cache; no
  broad home-directory mounts; no secret requirement for Tier 0.

CI should call the **same command surface** as local agents (`just agent::gate`
or `dagger call gate`) instead of duplicating logic in workflow YAML. This
runtime does **not** implement per-repo validation logic or Dagger pipelines —
that is the consumer/pilot repo's responsibility.

## Worktree Safety

Validation must be safe in concurrent worktrees:

- Resolve the repo root through git, never by assuming the current directory.
- Do not mutate sibling worktrees or write global state (except explicit
  bootstrap/install commands).
- Use per-repo / per-worktree temp and cache paths.
- Avoid destructive cleanup unless scoped to a temp directory the current command
  created.

## Using the Resolver

```sh
# Resolve + run a repo's explicit contract (fail-closed on unmigrated repos):
phase-loop repo-validate fast
phase-loop repo-validate gate
phase-loop --repo /path/to/repo repo-validate full

# Environment / contract report (always exits 0):
phase-loop repo-validate doctor          # or: repo-validate check

# Machine-readable resolution (INSPECTION ONLY — prints the resolved plan and
# does NOT execute the repo-local command; returns 0 when a runnable contract is
# resolved, or the terminal code 20/21 when there is nothing to run). Omit --json
# to actually run the target:
phase-loop --json repo-validate gate
```

The resolver is dependency-light (Python stdlib only) and its planning stage is
pure: discovery and exit-code mapping are unit-tested without a live `just` or
package manager (see
[`../phase-loop-runtime/tests/test_repo_validation.py`](../phase-loop-runtime/tests/test_repo_validation.py)).

## Scope

agent-harness owns the **contract + neutral resolver + tests**. It intentionally
does **not** ship per-repo checks, affected-test mappings, or Dagger modules —
those belong to each consuming repo and to the governed-pipeline pilot that
proves the contract end to end.
