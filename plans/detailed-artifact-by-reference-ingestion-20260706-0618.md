# Detailed plan: advisor-board artifact-by-reference ingestion ("reference, don't inline")

## Task
Stop callers from inlining huge (20k+ token) artifacts/prompts into `invoke_panel(artifact: str)`, which chokes the *caller's* context. Promote `PanelRequest.artifact_ref` to real by-reference ingestion (runtime reads paths from disk), allow the review brief to be a file, add a crash-residual scratch-dir GC, add a soft size guardrail that steers callers to paths, and rewrite the `claude-advisor-board` skill to lead with "point at files by path." Keep `artifact: str` working (back-compat) and the golden byte-identity of the default board intact. First application of the harness-wide "reference, don't inline" principle.

## Research summary
All ingestion lives in `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`. Findings verified against `origin/main` (`4d7cf87`):
- **The leg prompt is already lean.** `_render_leg_prompt` (~line 307) does NOT inline the artifact — it stages it as `review-bundle.md` and instructs the leg to *read the file* ("intentionally staged as a Markdown file instead of being pasted into the prompt"). So the bloat is **only** at the caller→runtime boundary: the caller builds `artifact: str` (the full content) to call `invoke_panel`.
- **`artifact_ref` is declared only** (`PanelRequest.artifact_ref`, ~line 128) — nothing reads it. Greenfield to wire.
- **The bundle is staged from the string** in `_default_spawn` (~line 1137): `base = tempfile.mkdtemp(prefix="pl-panel-")`; `(review_dir/"review-bundle.md").write_text(artifact)` (~1144); `(review_dir/"review-instructions.md").write_text(_mode_instructions(mode))` (~1145). The brief written is `_mode_instructions(mode)` — a caller-provided brief file is NOT currently supported.
- **Per-run cleanup exists**: `_default_spawn` wraps staging+spawn in `try/…/finally: shutil.rmtree(base, ignore_errors=True)` (~1172). But 5 stray `pl-panel-*` dirs exist in `/tmp` → they leak when the process is killed before `finally` (timeout/crash). So the gap is *crash-residual*, not missing cleanup.
- **Size is already measured**: `panel_leg_timeout_seconds(leg, artifact)` (~117) scales the timeout by `len(artifact.encode())`; `_artifact_metadata` (~302) returns `(sha256, byte_len)`.
- **Golden invariant** (`tests/test_advisor_board_golden.py`): Proof A asserts the default board's per-leg **argv + scrubbed env + timeout** are byte-equal between `invoke_panel(artifact, PANEL_LEGS)` and `invoke_board(DEFAULT_BOARD)`; sole sanctioned delta is `seat_key`. Timeout scales by artifact byte-size, so path-ingestion must reproduce the **exact same bytes** the inline string would have.

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py` (modify)
- `_resolve_artifact(artifact: str | None, artifact_ref: str | Sequence[str] | None) -> str` — **add** (place near `_artifact_metadata`, ~302) — the single ingestion hook. If `artifact_ref` is set: read each path with `Path(p).read_text(encoding="utf-8", errors="replace")` and, for multiple paths, concatenate deterministically with a per-file header (`f"## {Path(p).name}\n{content}"` joined by `\n\n`); return that. If `artifact_ref` is `None`: return `artifact or ""`. If both set: `artifact_ref` wins (document it). Raise a clear `ValueError` naming the missing path if a ref path doesn't exist (fail-closed, not silent-empty). — *reason: promotes `artifact_ref` to real by-reference ingestion; caller passes paths, runtime reads.*
- `_resolve_brief(mode: str, brief_ref: str | None) -> str` — **add** — returns `Path(brief_ref).read_text(...)` when set, else `_mode_instructions(mode)` (today's behavior). — *reason: brief-in-a-file (part 2).*
- `invoke_panel` (~1259) — **modify** signature: add keyword-only `artifact_ref: str | Sequence[str] | None = None, brief_ref: str | None = None`; at the top, `artifact = _resolve_artifact(artifact, artifact_ref)` before any use (so timeout/staging/metadata all see resolved content). — *reason: caller-facing path option; string path preserved when refs are None.*
- `invoke_board` (~1449) — **modify** signature identically (`artifact_ref`, `brief_ref`), resolve `artifact` at top, thread `brief_ref` to the spawn. — *reason: same for the board entry.*
- `invoke_panel_request` (~1306) — **modify**: read `request.artifact_ref` through `_resolve_artifact(request.artifact, request.artifact_ref)` instead of using `request.artifact` raw. — *reason: makes the already-declared `PanelRequest.artifact_ref` actually functional.*
- `_default_spawn` (~1137) — **modify**: stage the brief via `_resolve_brief(mode, brief_ref)` into `review-instructions.md` instead of hardcoded `_mode_instructions(mode)`; thread `brief_ref` through `_default_spawn_via_provider` (~1184) and `spawn(...)` call sites. — *reason: brief-in-a-file reaches the staging site.*
- `_maybe_warn_inline_size(artifact: str, *, from_ref: bool) -> None` — **add** — if `not from_ref` and `len(artifact.encode()) > _MAX_INLINE_ARTIFACT_BYTES`, `logging.getLogger(__name__).warning(...)` pointing to `artifact_ref` ("large inline artifact (%d bytes) — pass artifact_ref=<path> to keep caller context lean"). **Warn, not refuse** (refusing breaks existing callers; a soft nudge steers). Call it in `invoke_panel`/`invoke_board` after `_resolve_artifact`. — *reason: the size guardrail (part 4).*
- `_MAX_INLINE_ARTIFACT_BYTES = 16 * 1024` — **add** module constant (~16 KB ≈ a few thousand tokens; anything larger should have been a file). — *reason: the threshold.*
- `_gc_stale_panel_scratch(root: Path = Path(tempfile.gettempdir()), max_age_s: int = 24*3600) -> None` — **add** — best-effort sweep: for each `root/pl-panel-*` older than `max_age_s` (mtime), `shutil.rmtree(..., ignore_errors=True)`. Call once at the top of `_default_spawn` (before `mkdtemp`), wrapped so a GC failure never affects the run. — *reason: reclaims crash-residual dirs (the 5 stray ones), addressing pile-up without touching the working per-run `finally`.*

### `~/.claude/skills/claude-advisor-board/SKILL.md` (modify) — canonical source in agent-harness `phase-loop-skills/` + `skills_bundle/`
- `## Use` / `## Standalone Smoke Shape` — **modify** — lead with: "**Point at artifact files by path (`artifact_ref`); do not paste content into the call.** Compose any large brief in a file and pass `brief_ref`. The runtime reads and stages them — your context stays lean." Change the smoke example from `invoke_panel(artifact, available_panel_legs())` to `invoke_panel("", available_panel_legs(), artifact_ref="path/to/bundle.md")`. — *reason: stop advertising the inline path; steer to references (part 5).* NOTE: edit the **canonical** source under `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/claude-advisor-board/` (and the neutral `phase-loop-skills/` copy per the repo's parity gate), not the installed symlink.

## Documentation impact
- `phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md` — modify — note `artifact_ref`/`brief_ref` by-reference ingestion + the "reference, don't inline" contract + the size-guard threshold.
- `docs/advisor-board-capabilities-card.md` — modify — add a line: artifacts/briefs are passed by path (`artifact_ref`/`brief_ref`); inline is discouraged for large content.
- `CHANGELOG.md` — modify — `[Unreleased]` entry: "Advisor board reference-not-inline ingestion (`artifact_ref`/`brief_ref` path ingestion, inline-size guardrail, scratch GC)." (docs-freshness gate needs this.)

## Dependencies & order
1. `_resolve_artifact` + `_resolve_brief` helpers first (pure functions, no callers yet).
2. Wire into `invoke_panel` / `invoke_board` / `invoke_panel_request` (resolve at top) + thread `brief_ref` through `_default_spawn`/`_default_spawn_via_provider`.
3. Size guard (`_maybe_warn_inline_size` + constant) — depends on knowing `from_ref` (step 2).
4. Scratch GC (`_gc_stale_panel_scratch`) — independent; wire into `_default_spawn`.
5. Skill rewrite + docs — independent (can land with or after the code).

## Verification
```bash
cd phase-loop-runtime
# path ingestion is byte-transparent vs inline (same argv/env/timeout/staged bundle):
PYTHONPATH=src python3 -m pytest tests/test_advisor_board_ingestion.py -q      # NEW test file
# golden byte-identity STILL holds (the release keystone):
PYTHONPATH=src python3 -m pytest tests/test_advisor_board_golden.py -q
# nothing else regressed:
PYTHONPATH=src python3 -m pytest tests/ -q -k 'advisor_board or governed or backcompat'
```
New tests (`tests/test_advisor_board_ingestion.py`):
- `artifact_ref` (single path) reads the file → staged `review-bundle.md` byte-equals `invoke_panel(artifact=<same content>)`; `panel_leg_timeout_seconds` identical (same bytes → same timeout).
- multi-path `artifact_ref` concatenates deterministically (stable order + per-file header).
- a non-existent `artifact_ref` path raises `ValueError` naming the path (fail-closed, not silent-empty).
- `brief_ref` stages as `review-instructions.md` (content equals the file).
- size guard: an inline artifact > 16 KB emits exactly one warning (assert via `caplog`/`assertLogs`); ≤ threshold emits none; the guard never mutates content or blocks.
- scratch GC: seed a stale `pl-panel-<old>` dir with an old mtime under a temp root → `_gc_stale_panel_scratch(root)` removes it; a fresh one survives; a GC error is swallowed.
Edge cases: `artifact_ref` given AND `artifact` given → ref wins (documented); empty string + no ref → empty bundle (today's behavior); relative vs absolute ref paths.

## Acceptance criteria
- [ ] `invoke_panel("", legs, artifact_ref=P)` where `P` holds content `C` stages `review-bundle.md` byte-identical to `invoke_panel(C, legs)`, with identical per-leg argv/env/timeout.
- [ ] `tests/test_advisor_board_golden.py` passes unchanged (default board byte-identity preserved; `artifact: str` back-compat intact).
- [ ] An inline `artifact` > `_MAX_INLINE_ARTIFACT_BYTES` logs exactly one steering warning and still runs (warn, never refuse).
- [ ] `_gc_stale_panel_scratch` removes a stale `pl-panel-*` dir and preserves a fresh one; a GC failure does not affect the run.
- [ ] The `claude-advisor-board` skill's canonical source leads with the path-based instruction and its example uses `artifact_ref`.

## Execution Policy
- execute: effort=medium, reason=single-file runtime change with a concurrency-adjacent staging path + a load-bearing golden byte-identity invariant to preserve; not mechanical, not deep.
