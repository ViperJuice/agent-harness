# DFPARSOAK

## Lane Index & Dependencies

- SL-0 - Contract fixture writer; Depends on: (none); Blocks: SL-3; Parallel-safe: yes
- SL-1 - Harness fixture writer; Depends on: (none); Blocks: SL-3; Parallel-safe: yes
- SL-2 - Closeout fixture writer; Depends on: (none); Blocks: SL-3; Parallel-safe: yes
- SL-3 - Receipt reducer; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Contract fixture writer

- **Owned files**: `vendor/phase-loop-runtime/tests/fixtures/phase_loop_soak/contract-lane-output.json`
- **Interfaces provided**: `DFPARSOAK-contract-lane-output`
- **Interfaces consumed**: `DFPARSOAK-soak-input-contract`, `GFPARSOAK-receipt-citation`
- **Parallel-safe**: yes

### SL-1 - Harness fixture writer

- **Owned files**: `vendor/phase-loop-runtime/tests/fixtures/phase_loop_soak/harness-lane-output.json`
- **Interfaces provided**: `DFPARSOAK-harness-lane-output`
- **Interfaces consumed**: `DFPARSOAK-soak-input-contract`, `GPPARSOAK-receipt-citation`
- **Parallel-safe**: yes

### SL-2 - Closeout fixture writer

- **Owned files**: `vendor/phase-loop-runtime/tests/fixtures/phase_loop_soak/closeout-lane-output.json`
- **Interfaces provided**: `DFPARSOAK-closeout-lane-output`
- **Interfaces consumed**: `phase_loop_closeout.v1`, `scheduler_lane_assignment.v1`
- **Parallel-safe**: yes

### SL-3 - Receipt reducer

- **Owned files**: none
- **Interfaces provided**: `DFPARSOAK-substrate-receipt`
- **Interfaces consumed**: `DFPARSOAK-contract-lane-output`, `DFPARSOAK-harness-lane-output`, `DFPARSOAK-closeout-lane-output`
- **Parallel-safe**: no

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-0: executor=`codex`, model=`gpt-5.6-sol`, effort=`medium`, work-unit=`lane_execute`, unsupported=`fallback`, fallback=`registry-default-inheritance`
- SL-1: executor=`codex`, model=`gpt-5.6-sol`, effort=`medium`, work-unit=`lane_execute`, unsupported=`fallback`, fallback=`registry-default-inheritance`
- SL-2: executor=`codex`, model=`gpt-5.6-sol`, effort=`medium`, work-unit=`lane_execute`, unsupported=`fallback`, fallback=`registry-default-inheritance`
