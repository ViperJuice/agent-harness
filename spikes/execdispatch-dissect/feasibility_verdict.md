# Per-harness feasibility verdict (IF-0-DISSECT-1)

Grounded in an **actual extraction run** against a real second harness (codex),
plus a light real peek at grok and agy. This is the evidence record for the
north-star **B1** threshold and the B2 note.

---

## B1 verdict — ANSWER: **YES**

> Are IF-0-DISSECT-1 v1 profiles extractable for **>= 2 harnesses**
> (claude-code + >= 1 verified by an actual extraction run)?

**YES — the two harnesses are `claude-code` and `codex`.** Both were extracted by
a real run of `extract_profile.py` and both datasets validate (schema gate +
redaction gate, exit 0) against the **same** `schema.v1.json`:

- `tool_usage_profile.json` (claude) — 27 rows, PASS.
- `tool_usage_profile.codex.json` (codex) — 10 rows, PASS.

This is not a format read-through; it is two committed, schema-valid datasets
produced from real session transcripts.

---

## Harness 1 — claude-code (primary)  → EXTRACTABLE

- **Source:** `~/.claude/projects/**/*.jsonl` (363 project dirs, ~5003 files).
- **What was run:** `extract_profile.py --harness claude --sample 48 --seed 1729`.
- **Result:** 43/48 sampled files with tool calls, **3,177 calls, 19 tools**,
  27 `(task_type, tool_name)` rows; validates against `schema.v1.json`.
- **Structure:** `type:"assistant"` -> `message.content[]` -> `tool_use{name, input}`,
  where `input` is already a dict. Richest of the three formats.
- **Blockers:** none.

## Harness 2 — codex (the mandated real second extraction)  → EXTRACTABLE

- **Source:** `~/.codex/archived_sessions/rollout-*.jsonl` (551 files).
- **What was run:** `extract_profile.py --harness codex --sample 40 --seed 1729`.
- **Result:** 30/40 sampled files with tool calls, **2,319 calls, 10 tools**,
  10 rows; validates against `schema.v1.json` (same schema as claude).
- **Structure:** `type:"response_item"` -> `payload{type, name, arguments|input}`.
  Tool calls arrive as `function_call` (`name` + `arguments`), `custom_tool_call`
  (`name` + `input`), and `web_search_call` (type only). Top tools:
  `exec_command` (1,942), `apply_patch` (201), `write_stdin` (133),
  `view_image`, `update_plan`, `gateway_catalog_search`.
- **Concrete blockers found (real, non-fatal):**
  1. **`function_call.arguments` is a JSON *string***, not an object — must be
     `json.loads`-parsed before shaping. Handled in `_coerce_args`.
  2. **`apply_patch` sends a non-JSON body** (a raw patch), so its `arg_keys` is
     legitimately empty — the extractor degrades to `{}` rather than leaking the
     patch text. A future codex-specific profiler could special-case the patch
     envelope, but it is not required for a schema-valid profile.
  3. Tool names are codex-native (`exec_command`, not `Bash`) — expected and
     desirable per north-star Principle 2 (tool naming is per-MODEL).
- **Overall:** fully extractable; blockers are format-normalization details, all
  handled.

## Harness 3 — grok (B2-relevant; findable + structured, real peek done)  → EXTRACTABLE (in principle)

- **Source:** `~/.grok/sessions/<url-encoded-cwd>/<session-id>/events.jsonl`
  (also `updates.jsonl`). `prompt_history.jsonl` exists too but holds **raw
  prompt bodies** — that file is off-limits for a redacted profile; the
  structured events file is the right source.
- **What was run (light real peek, not a committed profile):** parsed
  `tool_started` events across 20 sampled `events.jsonl` files.
- **Result:** 38 `events.jsonl` files found; 17/20 sampled sessions had tool
  activity; **379 `tool_started` events**. Tool distribution:
  `read_file` (167), `run_terminal_command` (143), `grep` (55), `list_dir` (8),
  `get_command_or_subagent_output`, `kill_command_or_subagent`, `todo_write`.
- **Note (B2):** grok session data **is present and structured** with an explicit
  `tool_name` on `tool_started`/`tool_completed` events — a positive signal for
  north-star **B2** (pi+grok distilled agent). Grok's names are model-native
  (`read_file`/`run_terminal_command`/`grep`), distinct from claude's
  (`Read`/`Bash`/`Grep`) and codex's (`exec_command`), which is exactly the
  per-model tool-naming that Principle 2 requires. A full grok profiler would
  read the event stream (argument shapes live on the `tool_started`/`permission_*`
  payloads rather than a single `arguments` field) — a modest adapter, not a
  blocker. Not run to a committed profile here because codex is the mandated
  second harness and the time-box favors verdict quality over a third dataset.

## Harness 4 — agy / gemini (noted, not run)  → LIKELY EXTRACTABLE

- **Source:** `~/.gemini/tmp/**/chats/session-*.jsonl` carry `functionCall`
  markers (8 such files found); `~/.gemini/antigravity-cli/history.jsonl` also
  present. Structured tool-call data exists.
- **Status:** not extracted in this spike (codex was the mandated second run).
  A `--harness agy` adapter over the `functionCall` shape is the follow-up.

---

## Summary table

| Harness | Session source | Real run this spike | Extractable | Committed v1 profile |
|---------|----------------|---------------------|-------------|----------------------|
| claude-code | `~/.claude/projects/**/*.jsonl` | YES (48 files, 3177 calls) | YES | `tool_usage_profile.json` |
| codex | `~/.codex/archived_sessions/rollout-*.jsonl` | YES (40 files, 2319 calls) | YES | `tool_usage_profile.codex.json` |
| grok | `~/.grok/sessions/**/events.jsonl` | peek (20 files, 379 tool_started) | YES (in principle) | — (B2 follow-up) |
| agy/gemini | `~/.gemini/tmp/**/chats/session-*.jsonl` | no | likely | — (follow-up) |

**B1: MET** (claude-code + codex, both by real extraction, both schema-valid).
**B2 signal: positive** (grok session data present, structured, model-native tool
names) — full grok profile is a small follow-up, not gated by any blocker found.
