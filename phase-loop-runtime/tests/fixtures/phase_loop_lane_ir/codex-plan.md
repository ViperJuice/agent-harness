---
phase_loop_plan_version: 1
phase: CODEX
roadmap: specs/phase-plans-v1.md
roadmap_sha256: fixture
---

# CODEX

## Lanes

### SL-0 - Codex Lane

- **Owned files**: `codex.md`
- **Interfaces provided**: `codex-fixture`
- **Interfaces consumed**: pre-existing runner contract
- **Parallel-safe**: no
- **Tasks**:
  - test: Parse the Codex lane shape.
  - impl: Keep this as a fixture.
  - verify: `python3 -m unittest test_phase_loop_lane_ir_fixtures`
