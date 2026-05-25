from __future__ import annotations

"""Pipeline adapter helpers.

The hard-coded v32 governed-pipeline ratification trigger contract is
`.pipeline/ratification-trigger.json`.
"""

from .flag import branchgov_enabled
from .markers import detect_pipeline_mode

__all__ = ("branchgov_enabled", "detect_pipeline_mode")
