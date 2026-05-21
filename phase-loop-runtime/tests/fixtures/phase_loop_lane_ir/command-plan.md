---
phase_loop_plan_version: 1
phase: COMMAND
roadmap: specs/phase-plans-v1.md
roadmap_sha256: fixture
---

# COMMAND

## Lanes

### CMD-0 - Command Lane

- **Owned files**: none
- **Interfaces provided**: `command-fixture`
- **Interfaces consumed**: pre-existing runner contract
- **Parallel-safe**: yes
- **Tasks**:
  - test: Parse the command adapter lane shape.
  - impl: Keep this as a fixture.
  - verify: `python3 -m unittest test_phase_loop_lane_ir_fixtures`
