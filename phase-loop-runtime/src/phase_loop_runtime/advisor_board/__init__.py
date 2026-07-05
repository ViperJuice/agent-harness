"""Advisor Board — frozen contracts (Phase 1 ABDFREEZE).

Interface-only preamble for the model-first, multi-harness Advisor Board
(specs/phase-plans-v5.md). This package ships the typed contracts + importable
stubs + shared canonical fixtures the parallel fan-out (ABDREG / ABDRESOLVE /
ABDHOME) codes against. It is ADDITIVE and behavior-neutral: nothing here touches
the running ``panel_invoker`` path.

Frozen gates (see ``CONTRACTS.md``):
* IF-0-ABDFREEZE-1 — seat/board schema, config location, per-harness model/effort
  mapping, host-leg identity, seat -> vendor-family projection (``schema`` +
  ``harness_mapping``).
* IF-0-ABDFREEZE-2 — registry interfaces + (model x harness) matrix API + shared
  canonical fixtures (``registries`` + ``fixtures``).
* IF-0-ABDFREEZE-3 — provider-backing selector + auth-enforcement (active env
  scrubbing) contract (``backing``).
* IF-0-ABDFREEZE-4 — back-compat: the ``default`` board reproduces today's 3-leg
  behavior (``fixtures.DEFAULT_BOARD`` + the golden-test scaffold).
* IF-0-ABDFREEZE-5 — internal advisor-board event envelope + best-effort
  forwarding (``events``); its concrete sink mapping lands in ABDOBS.

ABDOBS (Phase 6) then adds ``observability``: the envelope → omniagent-plus
state-ledger (``state_ledger_record.v0.1`` / ``runtime_event.v0.1``) mapping, an
:class:`~observability.AsyncForwardingSink` for off-thread best-effort dispatch,
and the ``LedgerWriter`` cross-language transport seam. This is the one place the
package touches ``panel_invoker`` — an OPT-IN ``sink=`` on ``invoke_board``;
``sink=None`` stays byte-neutral.
"""
from __future__ import annotations

from .schema import (
    AUTH_API_KEY,
    AUTH_LANES,
    AUTH_SUBSCRIPTION,
    BACKING_HOMEBREW,
    BACKING_OMNIGENT,
    CONFIG_RELATIVE_PATH,
    EFFORT_LEVELS,
    PROVIDER_BACKINGS,
    Board,
    HostContext,
    Seat,
    board_config_path,
    identify_host_leg,
    seat_vendor_family,
    vendor_family,
    vendor_of_harness,
    vendor_of_model,
)
from .harness_mapping import (
    EffortMappingError,
    MECH_CONFIG,
    MECH_FLAG,
    MECH_MODEL_NAME,
    SeatInvocation,
    gemini_base_model,
    render_gemini_model,
    render_seat_invocation,
)
from .registries import (
    DEFAULT_HARNESS_REGISTRY,
    DEFAULT_MODEL_REGISTRY,
    AuthAvailability,
    CompatibilityMatrix,
    DefaultHarnessRegistry,
    DefaultModelRegistry,
    HarnessRegistry,
    HarnessSpec,
    MatrixVerdict,
    ModelRegistry,
    ModelSpec,
    StubCompatibilityMatrix,
    StubHarnessRegistry,
    StubModelRegistry,
    UnknownHarnessError,
    UnknownModelError,
)
from .matrix import (
    DefaultCompatibilityMatrix,
    default_matrix,
)
from .presets import (
    DEFAULT_BOARD_NAME,
    PRESET_NAMES,
    PRESETS,
    get_preset,
)
from .config import (
    BoardConfig,
    BoardConfigError,
    load_boards,
)
from .backing import (
    VENDOR_API_KEY_VARS,
    BackingDecision,
    all_vendor_key_vars,
    resolve_seat_env,
    select_backing,
)
from .events import (
    EVENT_KINDS,
    EVENT_SCHEMA_VERSION,
    AdvisorBoardEvent,
    EventSink,
    NullSink,
    best_effort_forward,
)
from .observability import (
    LEDGER_RECORD_KIND,
    LEDGER_RECORD_SCHEMA,
    RUNTIME_EVENT_SCHEMA,
    RUNTIME_FAILURE_SCHEMA,
    WORKLOAD_BOARD,
    WORKLOAD_PHASE_EXECUTION,
    AsyncForwardingSink,
    BoardObserver,
    CollectingSink,
    JsonlLedgerWriter,
    LedgerWriter,
    StateLedgerSink,
    map_event_to_ledger_record,
    map_event_to_runtime_event,
    new_session_id,
)
from .fixtures import (
    CANONICAL_INVALID_PAIRS,
    CANONICAL_LEG_ORDER,
    CANONICAL_VALID_PAIRS,
    DEFAULT_BOARD,
    DEFAULT_SEAT_EFFORT_ARGS,
    DEFAULT_SEAT_RENDERED_MODEL,
    DEFAULT_SEATS,
    TWO_SAME_VENDOR_BOARD,
)
from .resolver import (
    BOARD_ALIASES,
    STANDIN_BOARDS,
    BoardResolutionError,
    BoardResolver,
    SeatSpecError,
    key_results_by_seat,
    parse_seat_spec,
    parse_seats,
    resolve_board,
    seat_result_key,
)
from .validation import (
    SeatValidationError,
    SeatVerdict,
    validate_board,
    validate_seat,
)

__all__ = [
    # schema
    "Seat",
    "Board",
    "HostContext",
    "EFFORT_LEVELS",
    "AUTH_LANES",
    "AUTH_SUBSCRIPTION",
    "AUTH_API_KEY",
    "PROVIDER_BACKINGS",
    "BACKING_HOMEBREW",
    "BACKING_OMNIGENT",
    "CONFIG_RELATIVE_PATH",
    "board_config_path",
    "identify_host_leg",
    "seat_vendor_family",
    "vendor_family",
    "vendor_of_harness",
    "vendor_of_model",
    # harness mapping
    "SeatInvocation",
    "render_seat_invocation",
    "render_gemini_model",
    "gemini_base_model",
    "EffortMappingError",
    "MECH_FLAG",
    "MECH_CONFIG",
    "MECH_MODEL_NAME",
    # registries
    "HarnessSpec",
    "ModelSpec",
    "AuthAvailability",
    "MatrixVerdict",
    "HarnessRegistry",
    "ModelRegistry",
    "CompatibilityMatrix",
    "StubHarnessRegistry",
    "StubModelRegistry",
    "StubCompatibilityMatrix",
    # registries — populated (ABDREG)
    "DefaultHarnessRegistry",
    "DefaultModelRegistry",
    "DEFAULT_HARNESS_REGISTRY",
    "DEFAULT_MODEL_REGISTRY",
    "UnknownHarnessError",
    "UnknownModelError",
    # matrix (ABDREG) — seat/board validation is exported from validation below
    "DefaultCompatibilityMatrix",
    "default_matrix",
    # presets + config loader (ABDREG)
    "PRESETS",
    "PRESET_NAMES",
    "DEFAULT_BOARD_NAME",
    "get_preset",
    "BoardConfig",
    "BoardConfigError",
    "load_boards",
    # backing + auth
    "VENDOR_API_KEY_VARS",
    "all_vendor_key_vars",
    "BackingDecision",
    "select_backing",
    "resolve_seat_env",
    # events
    "AdvisorBoardEvent",
    "EventSink",
    "NullSink",
    "best_effort_forward",
    "EVENT_KINDS",
    "EVENT_SCHEMA_VERSION",
    # observability (ABDOBS)
    "WORKLOAD_BOARD",
    "WORKLOAD_PHASE_EXECUTION",
    "LEDGER_RECORD_SCHEMA",
    "RUNTIME_EVENT_SCHEMA",
    "RUNTIME_FAILURE_SCHEMA",
    "LEDGER_RECORD_KIND",
    "new_session_id",
    "map_event_to_runtime_event",
    "map_event_to_ledger_record",
    "LedgerWriter",
    "JsonlLedgerWriter",
    "StateLedgerSink",
    "CollectingSink",
    "AsyncForwardingSink",
    "BoardObserver",
    # fixtures
    "DEFAULT_BOARD",
    "DEFAULT_SEATS",
    "DEFAULT_SEAT_RENDERED_MODEL",
    "DEFAULT_SEAT_EFFORT_ARGS",
    "CANONICAL_LEG_ORDER",
    "CANONICAL_VALID_PAIRS",
    "CANONICAL_INVALID_PAIRS",
    "TWO_SAME_VENDOR_BOARD",
    # resolver (ABDRESOLVE)
    "BoardResolver",
    "resolve_board",
    "parse_seat_spec",
    "parse_seats",
    "SeatSpecError",
    "BoardResolutionError",
    "BOARD_ALIASES",
    "STANDIN_BOARDS",
    "seat_result_key",
    "key_results_by_seat",
    # validation (ABDRESOLVE)
    "validate_seat",
    "validate_board",
    "SeatValidationError",
    "SeatVerdict",
]
