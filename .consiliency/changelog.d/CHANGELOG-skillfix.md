### SKILLFIX (agent-harness#87) — `claude-plan-detailed` Plan-Mode independence + `.consiliency/` default

- **`claude-plan-detailed` is usable outside Plan Mode.** Non-Plan-Mode invocation is
  now a first-class path: the skill writes the plan artifact + handoff without calling
  `ExitPlanMode` or gating on a plan-approval flow. When Plan Mode is active it still
  calls `ExitPlanMode` (plan doc as the approval surface). When Plan Mode is inactive
  and the operator asked to implement, the skill continues into implementation in the
  same session; otherwise it stops and notes the output is a planning artifact only.
- **Default artifact path moved to `.consiliency/plans/`.** Detailed plans now default
  to `.consiliency/plans/detailed-<slug>-<YYYYMMDD-HHMM>.md` (dir auto-created) instead
  of `plans/` at repo root; `--output` still overrides. The close-out `git add` path is
  updated to match. The `plans/manifest.json` registry location is unchanged (the entry
  `file` field records the new `.consiliency/` artifact path).
- Claude-only skill source edit (`skills-src/claude/claude-plan-detailed/SKILL.md`);
  gemini/opencode/codex sources untouched. Regenerated `phase-loop-skills/` and the
  packaged `skills_bundle/` copy; `tests/test_skills_canon_parity.py` green.
