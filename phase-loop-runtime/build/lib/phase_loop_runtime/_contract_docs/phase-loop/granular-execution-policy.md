# Granular Execution Policy

PROFILEDOC freezes the v8 policy posture: high -> medium -> high. Roadmap and phase planning use high or xhigh-capable heavy profiles, lane execution uses medium effort by default, repair remains medium, and reducer, review, verify, skill-maintenance, and runner-owned closeout guidance use high effort. This document is the operator source of truth until v9 moves lane scheduling into runner-owned work units.

## Default profiles

| Product action | Work unit | Default posture | Gemini CLI default model |
| --- | --- | --- | --- |
| `roadmap` | `roadmap_build` | high planning | `pro` |
| `plan` | `phase_plan` | high planning | `pro` |
| `execute` | `lane_execute` | medium implementation | `auto` |
| `repair` | `repair` | medium repair | `auto` |
| `review` | `lane_review` | high review | `pro` |
| `maintain-skills` | `phase_verify` | high reducer/verify | `pro` |

Closeout stays runner-owned. Manual closeout remains the default, commit-only is the bounded preservation mode after verification, and automatic push is not promoted by PROFILEDOC.

## Policy precedence

Execution policy resolution is deliberately boring and fail-closed:

1. CLI/operator override.
2. phase-plan policy.
3. roadmap policy.
4. `Dispatch Hints`.
5. registry defaults.

An unsupported model, effort, executor, or capability blocks unless the active policy names an explicit fallback or says to inherit the provider default. Silent provider fallback is not allowed.

## Provider selection

Codex is the default live-supported executor for roadmap, plan, repair, review, and skill-maintenance work. It accepts normalized model and effort metadata directly through the phase-loop launcher.

Simple bounded scheduler-assigned lane execution defaults to Pi Agent. Claude or Anthropic model lanes are the exception: they default to Claude Code CLI unless a phase plan, roadmap, or operator policy explicitly selects a Pi-wrapped Claude route and records the override reason.

Claude Code is proof-blocked for autonomous live closeout. Operators may use Claude Code bridge skills for manual reentry, and runner-brokered Claude child work must stay typed, bounded, and limited to approved `execute`, `repair`, or `review` work.

Gemini CLI is live-supported through the GEMPROBE result `gemini_cli_config_unverified_but_usable`. Default Gemini dispatch uses the CLI's built-in routing aliases: `pro` for planning/review and `auto` for execution/repair. This preserves Gemini CLI fallback behavior and avoids hard-pinning `gemini-3.1-pro-preview` for normal runs. Run-local user-scope `modelConfigs.customAliases` with `thinkingConfig.thinkingLevel` remain available only for explicit phase-loop proof or policy runs that need that stronger thinking-level assertion. Codex and Gemini fallback routes are CLI-based and reason-coded; they must not silently switch to API-key command adapters unless policy explicitly selects `executor=command`. Future Gemini API adapter work is a separate roadmap decision if the CLI route stops meeting the contract.

Pi Agent is the preferred simple bounded lane runner when the phase-loop policy
selects `executor=pi`. It consumes repo-local `phase-loop-pi/**` and
`pi-config/**` material through a context-file launch, supports low/medium/high
effort mapping, and fails closed for unsupported effort or tool-policy requests
unless the phase policy explicitly provides fallback behavior.

OpenCode is live-supported with explicit permission posture and model provenance recorded in launch metadata. Use it for bounded child work only when the phase policy allows that executor.

The generic command adapter is experimental. It is context-file only, opt-in, and must not pretend arbitrary subprocesses support the same model, effort, auth, or closeout semantics as first-class harnesses.

## Provider rotation examples

Use Codex for the normal bounded path:

```bash
phase-loop run --repo <repo> --roadmap <roadmap> --executor codex --max-phases 1
```

Use Gemini CLI with the default routing contract:

```bash
phase-loop run --repo <repo> --roadmap <roadmap> --executor gemini --max-phases 1 --closeout-mode manual
```

Use OpenCode for an explicitly allowed phase:

```bash
phase-loop run --repo <repo> --roadmap <roadmap> --executor opencode --max-phases 1 --closeout-mode commit
```

Use Claude Code only with the current proof-blocked closeout posture:

```bash
phase-loop run --repo <repo> --roadmap <roadmap> --executor claude --max-phases 1 --closeout-mode manual
```

Use a generic command adapter only when the roadmap or phase plan names the wrapper and owned files:

```bash
phase-loop run --repo <repo> --roadmap <roadmap> --executor command --max-phases 1 --closeout-mode manual
```

## Metrics and provenance

Every launched work unit records selected harness, model, effort, profile source, policy source, override reason, fallback status, fallback reason, duration, and blocker class in `.phase-loop/metrics.jsonl` and run launch metadata. The metrics prove what was attempted; they do not expose prompts, secrets, OAuth material, command stdout that may contain credentials, or raw provider payloads.

During MIGRATELOOP, review `.phase-loop/metrics.jsonl` before provider
rotation or scheduler promotion. Serialized lane scheduling may compare
executor outcomes, but push remains explicit and must not become an automatic
result of a clean metric row.

## Dispatch Hints

`Dispatch Hints` remain valid when no richer execution policy is present. They may select allowed executors and required capabilities, but they do not authorize unmanaged parallel write targets, dangerous fallback credentials, or silent provider fallback.

`Execution Policy` is limited to model, effort, work-unit defaults,
lane-specific policy, fallback, policy source, and override reason. Supported
selectors are `work-unit defaults`, `roadmap`, `plan`, `execute`, `repair`,
`review`, `maintain-skills`, and lane selectors such as `SL-2`; reducer and
verification work uses lane selectors with `work-unit=phase_reducer` or
`work-unit=phase_verify`. Selectors such as `reduce` and `verify` remain
invalid, and policy precedence stays CLI/operator override, phase-plan policy,
roadmap policy, `Dispatch Hints`, then registry defaults. A silent downgrade
or silent provider fallback is forbidden without explicit policy support.

Policy examples must preserve standalone dotfiles workflows and keep
governed-pipeline closeout ingest, Portal projection, and Greenfield metadata-only authority refs as mediated boundaries, not direct dotfiles write targets. Executor policy never authorizes inferred writes to `.pipeline/**`,
governed-pipeline specs, Portal contracts, Greenfield authority files, provider
payloads, credentials, raw evidence, or legacy `.codex/phase-loop/` state.

DFTRUTHSOAK reducer and verification work use lane selectors with
`work-unit=phase_verify`; execution policy still only carries model, effort,
work-unit defaults, lane-specific policy, fallback, policy source, and override
reason. The final truth soak may report standalone closeout, pipeline-required
closeout, stale-input blockers, source-truth advisory hints, bridge fixtures,
and downstream mirror refs, but governed-pipeline remains the canonical
authority for refresh, replan, preflight block, Portal projection, and
Greenfield metadata-only authority decisions.

DFPROMPTSYNC freezes the prompt-safe field map in
`docs/phase-loop/dfpromptsync-contract-map.md` and the readiness receipt in
`docs/phase-loop/dfpromptsync-readiness.md`. Execution policy examples should
preserve machine-verified disjoint lanes, scheduler-owned worktree assignments,
Claude Code CLI exception wording, and CLI-based reason-coded Codex/Gemini
fallback without introducing API-key fallback or prompt-only containment
claims.

## V9 boundary

PROFILEDOC does not add runner-owned lane scheduling. v9 consumes this default profile and provenance contract before changing lane waves, review reducers, or child work-unit scheduling.

## DFPARSOAK Integrated Soak

DFPARSOAK policy examples use `docs/phase-loop/dfparsoak-receipt.md` and
`docs/phase-loop/dfparsoak-runbook.md` for final integrated soak evidence
instead of older downstream plan examples. The receipt preserves Pi default,
Claude Code CLI exception, Codex CLI fallback, and Gemini CLI fallback metadata
without introducing API-key fallback or prompt-only containment claims.
