<!-- CLEANSHIP Phase 2 EXECGOV — executor-governance fixes (#153, #154).
     Assembled into CHANGELOG.md by the RELEASE phase; one entry per fix. -->

- **Claude `subagent`/`agent_team` authoring actions auto-degrade to solo instead
  of an opaque TEAMGOV block (`ViperJuice/agent-harness#153`).** A claude run in
  `subagent` or `agent_team` mode whose sub-step is an authoring action
  (`plan`/`roadmap`/`maintain-skills` — the modes' `disallowed_actions`) previously
  terminated with the bare policy sentence "Claude &lt;mode&gt; mode is denied for
  `&lt;action&gt;` by TEAMGOV policy," even though team semantics are meaningless for a
  single authoring action. `build_claude_launch_spec` now AUTO-DEGRADES that case to
  solo and dispatches (the command is built with the solo tool policy and the
  recorded `claude_execution_mode` is `solo`). The authoring set is read from the
  mode's own `ClaudeTeamPolicy.disallowed_actions`, never re-hardcoded. Claude solo,
  non-claude executors, and `execute`/`repair`/`review` under a team mode are all
  unaffected.

- **The AUTO executor resolver no longer seeds claude for an authoring action under
  a team mode (`ViperJuice/agent-harness#153`).**
  `default_executor_resolver._gate_candidate` now consults claude's
  `claude_execution_policies`: on the AUTO path (no explicit executor hint), an
  authoring action under `subagent`/`agent_team` skips claude
  (`claude_authoring_disallowed_under_&lt;mode&gt;`) rather than letting an inert
  subagent flag in a Claude Code session force a run-from claude pick the launcher
  would then block. The seed-gate and the launcher degrade are LAYERED, not mutually
  exclusive: the gate removes claude from the AUTO default seed, but
  `resolve_dispatch_decision`'s fallback can still route claude for an authoring action
  when the seeded executor is session-degraded — and the launch-time auto-degrade is the
  backstop that dispatches claude-solo in that residual case rather than blocking. A
  residual (non-authoring) team block now carries actionable remediation in the runner
  terminal — it names the phase and the `--claude-execution-mode solo` /
  plan-the-phase-first escape hatches instead of surfacing the raw policy sentence.

- **grok `execute` runs with a `--disallowed-tools` deny-list that removes privileged
  non-coding built-ins (`ViperJuice/agent-harness#154`).** The grok `execute` leg now
  subtracts the scheduler (`scheduler_create`/`scheduler_delete`/`scheduler_list`/
  `monitor`) and image/video (`image_gen`/`image_edit`/`image_to_video`/
  `reference_to_video`) built-in families while keeping grok's coding tools
  (read/search + write/edit + terminal), so a headless execute leg cannot schedule
  work or generate media outside the phase-loop's governance. Scoped to `execute`;
  `review` keeps its stricter read-only `--tools` allow-list and `repair`/`roadmap`/
  `plan` keep the unrestricted write branch. `--permission-mode bypassPermissions`
  stays (headless writes auto-approve regardless). Live-proven against grok 0.2.93: a
  restricted execute leg reads `context.md` at its absolute path outside `--cwd` and
  writes a file. (The deny-list passes grok's real built-in tool ids, argv-verified; a
  runtime enumeration is consistent with the scheduler/image families being removed, but
  grok's self-report is best-effort, not authoritative — the load-bearing guarantee is
  the argv + the behavioral subagent tripwire below.)

  Mechanism + limitation (grok 0.2.93): the originally-specified `--tools` ALLOW-LIST
  is unusable for a write leg — grok force-adds `run_terminal_command`, whose default
  config trips `auto_background_on_timeout requires enabled_background` and aborts the
  session before launch — so a deny-list (which preserves grok's working default
  config) is used instead. `memory` is not a grok tool (it is the
  `--experimental-memory` flag, which the harness never passes, so it is already off).
  The subagent family CANNOT be disabled from the CLI: NEITHER `--disallowed-tools
  spawn_subagent` NOR the dedicated `--no-subagents` flag stops a headless leg from
  spawning (both verified BEHAVIORALLY — a forced spawn still succeeds with a live
  subagent_id under each). Both levers are passed anyway as forward-compat, and a
  BEHAVIORAL tripwire test (`test_grok_spawn_subagent_denial_tripwire`) forces a spawn
  and trips the moment a future grok blocks it — so the gap is documented, not silently
  over-claimed. This residual (subagent fanout) is tracked in the still-open #154.
