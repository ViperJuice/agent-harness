---
phase_loop_plan_version: 1
phase: GEMINI
roadmap: specs/phase-plans-v1.md
roadmap_sha256: fixture
---

# GEMINI

## Lanes

### GM-0 - Gemini Lane

- **Owned files**: `gemini.md`
- **Interfaces provided**: `gemini-fixture`
- **Interfaces consumed**: pre-existing runner contract
- **Parallel-safe**: no
- **Tasks**:
  - test: Parse the Gemini lane shape.
  - impl: Keep this as a fixture.
  - verify: `python3 -m unittest test_phase_loop_lane_ir_fixtures`
