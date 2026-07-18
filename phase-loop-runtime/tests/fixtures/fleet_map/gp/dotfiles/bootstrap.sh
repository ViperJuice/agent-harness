#!/usr/bin/env bash
# Fixture bootstrap.sh-style script: installs a pinned phase-loop-runtime
# subdirectory from the agent-harness repo via a git+ref pin.
set -euo pipefail

pip install "git+https://github.com/Consiliency/agent-harness.git@v0.1.12#subdirectory=phase-loop-runtime"
