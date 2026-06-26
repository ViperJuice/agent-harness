# Detailed plan: add CI to the public agent-harness repo

## Task

`ViperJuice/agent-harness` (public, Apache-2.0) currently has **no `.github/workflows/`**. It is the shared phase-loop runtime + skills package pinned by 4 consumers (dotfiles fleet, governed-pipeline, consiliency-portal, regenesis-monorepo). Stand up CI that (1) runs the runtime's standalone test suite, (2) gates release tag↔version consistency, (3) keeps the repo scrub-clean (gitleaks), and (4) fixes the committed-build-artifacts hygiene gap. Single bounded change — only new `.github/` + `.gitignore` + one optional script; **no source code is modified**.

## Research summary

(From `explore-runtime` + `explore-skills-scrub`, verified against the local checkout.)

- **Package**: `phase-loop-runtime/pyproject.toml` — `name = "phase-loop-runtime"`, `version = "0.1.0"` (line 7, static), `requires-python = ">=3.10"` (line 10), `build-backend = setuptools.build_meta`, scripts `phase-loop`/`codex-phase-loop` → `phase_loop_runtime.cli:main`. **No `[project.optional-dependencies]`** — no `test`/`dev` extra; core deps are `baml-py>=0.222,<0.223`, `pydantic>=2,<3`, `PyYAML>=6,<7`. So CI installs the package + `pytest` explicitly.
- **Version is duplicated in two static places that must agree**: `phase-loop-runtime/pyproject.toml:7` (`version = "0.1.0"`) and `phase-loop-runtime/src/phase_loop_runtime/__init__.py` (`__version__ = "0.1.0"`, around line 20). The CLI's `--version` / `version` subcommand sources `__init__.py`. **Known wart**: git tag `v0.1.1` exists but both version sources still read `0.1.0` — a tag↔version check is exactly what catches this.
- **Tests**: `phase-loop-runtime/tests/` (182 test files). Standalone command is `pytest -m "not dotfiles_integration"`; `phase-loop-runtime/tests/conftest.py` (`pytest_collection_modifyitems`, ~lines 48–63) skips `dotfiles_integration`-marked tests when no dotfiles tree is present, and pins suite env (`PHASE_LOOP_PROFILE_PLUGINS`, `PHASE_LOOP_SKILL_SOURCE_PLUGINS`, `PHASE_LOOP_CLAUDE_ROUTE=print`, clears `CI`). No network/venv isolation needed beyond a normal install.
- **Existing clean-room harness to reuse**: `phase-loop-runtime/scripts/gate_a_cleanroom.sh` (+ `_gate_a_probe.py`) builds a wheel, installs into an isolated venv with no dotfiles/user-site, and smoke-tests `phase-loop version/status/dry-run/execute --bundle`, asserting no dotfiles paths leak. Exits 0 on pass. This is a ready-made "the published wheel installs and works standalone" gate.
- **Hygiene**: **138 tracked build artifacts** under `phase-loop-runtime/build/lib/...`. **No `.gitignore` anywhere** in the repo.
- **Scrub**: no `.gitleaks.toml`, no CI, no scrub workflow. `phase-loop-runtime/src/phase_loop_runtime/redaction.py` has runtime-only metadata-scrub patterns (not a CI step). A fresh gitleaks config is needed; note that v0.1.1 made redaction-test fixtures **synthetic**, so a gitleaks allowlist may be needed for those fixture paths.
- **Skills**: `phase-loop-skills/` — 9 skills, each with `_overrides/{claude,codex,gemini,opencode}/`. Total tracked `SKILL.md` = **36** (≠ 9 base × 5), so override coverage is **uneven** — a lint would be useful but must first establish the actual invariant.

## Changes

### `.gitignore` (create — repo root)
- New file — add — ignore generated Python artifacts so they stop being committed: `build/`, `dist/`, `*.egg-info/`, `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.venv/`, `venv/`, `.coverage`, `htmlcov/`. Reason: the repo currently tracks 138 `build/lib/...` files and has no ignore rules.
- Paired action (not a file edit): `git rm -r --cached phase-loop-runtime/build` to untrack the existing artifacts (keeps them on disk, removes from the index). Reason: `.gitignore` alone does not untrack already-committed files.

### `.github/workflows/test.yml` (create)
- New workflow — add — `name: test`; triggers `on: [push to main, pull_request]`. One job `pytest`:
  - `strategy.matrix.python-version: ['3.10', '3.11', '3.12']` (matches `requires-python >=3.10`).
  - `actions/checkout@v4`, `actions/setup-python@v5` with the matrix version.
  - Install: `pip install ./phase-loop-runtime pytest` (no test extra exists; install the package + pytest).
  - Run: `cd phase-loop-runtime && pytest -m "not dotfiles_integration"` (conftest lives under `phase-loop-runtime/tests/`, so run from the package dir).
- Second job `cleanroom` (single OS/python): run the existing `phase-loop-runtime/scripts/gate_a_cleanroom.sh` to prove the wheel installs + smoke-tests standalone. Reason: reuse the maintained Gate-A harness as the install/identity gate instead of reinventing it. (Confirm the script needs no args + works in a clean CI runner; if it assumes tools not on the runner, document the prereqs.)

### `.github/workflows/release-consistency.yml` (create)
- New workflow — add — `name: release-consistency`; triggers `on: push: tags: ['v*']` **and** `pull_request` (so a version bump is validated before the tag). One job, no untrusted input:
  - Read `pyproject.toml` `[project].version` and `src/phase_loop_runtime/__init__.py` `__version__`; assert they are equal.
  - On a tag event, additionally assert the tag (from `${GITHUB_REF_NAME}` via `env:`, not inline `${{ }}` in the script) with any leading `v` stripped equals the package version.
  - Fail non-zero on any mismatch. Reason: the `v0.1.1`-tag-but-`0.1.0`-version bug is exactly this class; this prevents a recurrence.
  - **Note for the implementer**: adding this does NOT retroactively fix the existing `v0.1.1` tag (tag 0.1.1 vs version 0.1.0). That historical inconsistency is a release action to resolve separately (bump both version sources to 0.1.1 in a patch commit and re-tag, or cut 0.1.2) — out of scope for this CI plan; record it as an open item.

### `.github/workflows/scrub.yml` (create) + `.gitleaks.toml` (create — repo root)
- `scrub.yml` — new workflow — add — `name: scrub`; `on: [push to main, pull_request]`; runs `gitleaks` (pin a specific action SHA/tag, e.g. `gitleaks/gitleaks-action@<pinned>`, or the gitleaks docker image at a pinned digest) `detect --source . --config .gitleaks.toml --redact`. Reason: the repo was extracted from a private fleet repo and passed a Gate-B gitleaks audit; CI must keep it scrub-clean.
- `.gitleaks.toml` — new file — add — start from gitleaks' default ruleset; add an `[allowlist]` for the **synthetic** redaction-test fixtures introduced in v0.1.1 (locate them under `phase-loop-runtime/tests/` / `phase-loop-runtime/src/phase_loop_runtime/_test_fixtures/` and allowlist by path/regex) so known-synthetic tokens don't trip the gate. Reason: avoid false positives on deliberately-synthetic fixtures while still catching real secrets. **Implementer must verify** which fixture paths gitleaks flags by running it locally first (see Verification) and allowlist exactly those.

### `scripts/lint_skills.py` + `.github/workflows/skills-lint.yml` (create — OPTIONAL / P2)
- Optional — add — a small script asserting every `phase-loop-skills/*/SKILL.md` has `name:` + `description:` frontmatter, and (once the intended invariant is confirmed) that each skill carries the expected `_overrides/{claude,codex,gemini,opencode}/SKILL.md`. The current count (36 SKILL.md, uneven overrides) means the invariant must be **established with the maintainer first** — do not encode "all 9 × 4 overrides required" until confirmed, or the lint will fail legitimately-partial skills. Reason: cheap consistency guard, but lower priority than test/version/scrub and needs a decision on the invariant. Defer if it adds friction.

## Documentation impact
- `README.md` (modify) — add a short **Development / CI** section: the test command (`cd phase-loop-runtime && pytest -m "not dotfiles_integration"`), the supported Python range (`>=3.10`), and (optionally) status badges for the new workflows. Reason: the repo has no documented test/develop path today.
- No `CHANGELOG.md`/`CONTRIBUTING.md` exist; creating them is out of scope for this CI change.

## Dependencies & order
1. **`.gitignore` + `git rm --cached build` first** — so the test/scrub workflows don't run against (or scan) committed build artifacts.
2. **`test.yml`** next (the P0 gate; everything else is additive).
3. **`release-consistency.yml`** and **`scrub.yml` + `.gitleaks.toml`** are independent of each other; either order. `scrub.yml` requires `.gitleaks.toml` to exist first.
4. **skills-lint** last and optional — blocked on confirming the override invariant with the maintainer.
- No blocking external dependencies; all changes are new files in one repo. No source modules change, so there is zero blast radius into the runtime.

## Verification
```sh
# Runtime tests (what test.yml runs), per supported Python:
cd phase-loop-runtime && python -m pip install . pytest && pytest -m "not dotfiles_integration"
# expect: ~the v0.1.1 "781 passed" standalone run, 0 failures, integration tests skipped.

# Clean-room wheel smoke (what the cleanroom job runs):
phase-loop-runtime/scripts/gate_a_cleanroom.sh ; echo "exit=$?"   # expect exit 0

# Build-artifact untracking landed:
git ls-files phase-loop-runtime/build | wc -l                      # expect 0 after git rm --cached
git check-ignore phase-loop-runtime/build/lib                      # expect a match (ignored)

# Tag/version consistency check — prove it CATCHES the current inconsistency:
PYV=$(python - <<'PY'
import tomllib,pathlib
print(tomllib.loads(pathlib.Path("phase-loop-runtime/pyproject.toml").read_text())["project"]["version"])
PY
)
INITV=$(grep -oE '__version__ *= *"[^"]+"' phase-loop-runtime/src/phase_loop_runtime/__init__.py | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
echo "pyproject=$PYV init=$INITV"            # expect equal (0.1.0 == 0.1.0) -> PR check passes
echo "tag v0.1.1 -> 0.1.1 vs $PYV"           # demonstrates the tag-event check WOULD fail on v0.1.1 (intended)

# gitleaks locally before writing the allowlist (find what to allowlist):
gitleaks detect --source . --redact --report-format json --no-banner   # inspect findings; allowlist only synthetic fixtures

# Workflow YAML well-formed (lint without running):
for f in .github/workflows/*.yml; do python -c "import yaml,sys; yaml.safe_load(open('$f'))" && echo "ok $f"; done
```

## Acceptance criteria
- [ ] `.github/workflows/test.yml` exists; on a PR it runs `pytest -m "not dotfiles_integration"` on Python 3.10/3.11/3.12 and passes (0 failures), plus a clean-room `gate_a_cleanroom.sh` job that exits 0.
- [ ] `git ls-files phase-loop-runtime/build` returns nothing and `.gitignore` ignores `build/` (138 artifacts untracked).
- [ ] `release-consistency.yml` exists and, in a local/dry run, **passes** when `pyproject.toml` version == `__init__.py __version__`, and **fails** when a `v*` tag's version differs from the package version (verified against the `v0.1.1` case).
- [ ] `scrub.yml` + `.gitleaks.toml` exist; `gitleaks detect` reports **0 leaks** on the repo (synthetic test fixtures allowlisted by path, real-secret rules intact).
- [ ] No `${{ github.event.* }}` (or other untrusted input) is interpolated into any workflow `run:` block; tag/ref values reach scripts via `env:`.
- [ ] (Optional) skills-lint deferred or, if shipped, encodes only the maintainer-confirmed override invariant.
