---
phase_loop_plan_version: 1
phase: OPENCODE
roadmap: specs/phase-plans-v1.md
roadmap_sha256: fixture
---

# OPENCODE

## Lanes

### OP-0 - OpenCode Lane

- **Owned files**: `opencode.md`
- **Interfaces provided**: `opencode-fixture`
- **Interfaces consumed**: pre-existing runner contract
- **Parallel-safe**: no
- **Tasks**:
  - test: Parse the OpenCode lane shape.
  - impl: Keep this as a fixture.
  - verify: `python3 -m unittest test_phase_loop_lane_ir_fixtures`
