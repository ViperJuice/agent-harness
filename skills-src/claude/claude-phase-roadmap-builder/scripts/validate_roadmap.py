#!/usr/bin/env python3
"""validate_roadmap.py — mechanical lint for phase-plan roadmap specs.

Usage:
    validate_roadmap.py <roadmap-path>

Thin shim over `phase_loop_runtime.roadmap_lint`, which is the single source of
truth for roadmap validation and is always available wherever the phase-loop
runtime is installed. Prefer `phase-loop validate-roadmap <path>` directly; this
script remains for direct invocation and backward compatibility.
"""

from __future__ import annotations

import sys


def _main() -> int:
    try:
        from phase_loop_runtime.roadmap_lint import main
    except ModuleNotFoundError:
        sys.stderr.write(
            "validate_roadmap: phase_loop_runtime is not importable. Install the "
            "runtime from the public agent-harness (`scripts/install-agent-harness.sh`) "
            "or run `phase-loop validate-roadmap <path>`.\n"
        )
        return 2
    return main(sys.argv)


if __name__ == "__main__":
    sys.exit(_main())
