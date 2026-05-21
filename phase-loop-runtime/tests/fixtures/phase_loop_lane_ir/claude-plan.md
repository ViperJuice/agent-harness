---
phase_loop_plan_version: 1
phase: CLAUDE
roadmap: specs/phase-plans-v1.md
roadmap_sha256: fixture
---

# CLAUDE

## Lanes

### SG-0 - Claude Lane

- **Owned files**: `claude.md`
- **Interfaces provided**: `claude-fixture`
- **Interfaces consumed**: pre-existing runner contract
- **Parallel-safe**: no
- **Tasks**:
  - test: Parse the Claude lane shape.
  - impl: Keep this as a fixture.
  - verify: `python3 -m unittest test_phase_loop_lane_ir_fixtures`
