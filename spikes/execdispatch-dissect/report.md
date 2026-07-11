# SPIKE-DISSECT — claude-code session-data tool-usage profile

Research spike for the EXECDISPATCH roadmap (`specs/phase-plans-v8.md`, Phase 3 /
gate **IF-0-DISSECT-1**). Evidence probe for the north-star pi arc
(`specs/north-star-pi-native.md`, backlog **B1/B2**). **No production `src/` code
is touched** — everything lives under `spikes/execdispatch-dissect/`.

All artifacts are **metadata-only**: tool names, argument *keys*, argument
*shape/type*, and counts/frequencies. No raw argument values, file contents, or
prompt/message bodies are recorded or committed. The validator enforces this with
an active redaction gate (see below).

---

## 1. Deliverables

| File | What |
|------|------|
| `schema.v0.draft.json` | v0 DRAFT schema, spec-derived, committed **before** the extraction (ordering anchor). |
| `schema.v1.json` | Frozen v1 JSON Schema (draft-07 subset) after first real jsonl inspection. |
| `extract_profile.py` | Transcript parser + profile extractor (claude + codex). |
| `validate_profile.py` | Stdlib-only validator: schema gate + **independent semantic redaction gate**. |
| `tool_usage_profile.json` | claude-code dataset (v1-conformant). |
| `tool_usage_profile.codex.json` | codex dataset (v1-conformant; the real second-harness run). |
| `feasibility_verdict.md` | Per-harness extractability + the B1 verdict. |
| `report.md` | This file. |

Run the validator:

```
python3 spikes/execdispatch-dissect/validate_profile.py \
        spikes/execdispatch-dissect/tool_usage_profile.json \
        spikes/execdispatch-dissect/schema.v1.json
```

---

## 2. Sample size & methodology

- **Corpus:** `~/.claude/projects/**/*.jsonl` — 363 project directories,
  ~5003 transcript files on this host.
- **Sample:** 48 files, drawn with `random.Random(1729).sample(...)` over the
  **full sorted file list** (stride across all 363 project dirs, not `head -N`),
  for representativeness under a time-box. Selection is deterministic/reproducible
  via `--seed 1729`.
- **Yield:** 43 of 48 sampled files carried tool calls -> **3,177 tool calls**,
  **19 distinct tools**, 27 `(task_type, tool_name)` rows.
- **Extraction unit:** each `type:"assistant"` line's
  `message.content[]` blocks with `type:"tool_use"` contribute `(name, input)`.
  The `input` dict is reduced to a **shape** (types only) at read time — raw
  values never enter the committed dataset.

### task_type heuristic (documented)

`task_type` is derived from **tool-mix only** — never from project-dir names
(user paths) or prompt bodies (both off-limits for redaction). For each session:

1. Count calls per tool; map each tool to a category
   (`code-modification`, `exploration`, `execution`, `delegation-orchestration`,
   `web-research`, or `tooling-mcp` for `mcp__*` tools).
2. The category holding **>= 40 %** of the session's calls names the session;
   otherwise `mixed`; empty -> `unknown`.

`task_type` is a fixed enum in `schema.v1.json`. It is the one classification
input that is both empirical and redaction-clean.

`frequency` = a row's `call_count` / total calls in that `task_type` (a tool's
share within its task class).

---

## 3. claude-code profile — findings

**Top tools (by call count, whole sample):**

| Tool | Calls | Share |
|------|------:|------:|
| Bash | 1,976 | 62 % |
| Read | 485 | 15 % |
| Edit | 308 | 10 % |
| TaskUpdate | 93 | 2.9 % |
| Write | 86 | 2.7 % |
| Agent | 44 | 1.4 % |
| TaskCreate | 44 | 1.4 % |
| SendMessage | 30 | 0.9 % |

Long tail (<= 25 calls each): AskUserQuestion, Skill, TodoWrite, Glob, Grep,
WebFetch, ToolSearch, and `mcp__*` gateway tools.

**Per-task-type variation.** Calls split `execution` 2,915 / `exploration` 259 /
`mixed` 3. This host's claude-code sessions are overwhelmingly **execution-class**
(Bash-dominant orchestration work) — a real finding, not a classifier artifact:
Bash alone is 62 % of all calls. `Read` is the one tool that splits across
classes (293 execution + 192 exploration), i.e. the same tool appears under two
task types with the *same* argument shape — exactly the per-task narrowing signal
B1 needs (a distilled agent for an exploration task can drop the write/exec tools
and their arg surface).

**Argument-shape patterns (types only).** Shapes are stable per tool and recurse
cleanly:

- `Read` -> `{file_path: string, limit: integer, offset: integer}`
- `Edit` -> `{file_path: string, old_string: string, new_string: string, replace_all: boolean}`
- `Bash` -> `{command: string, description: string, run_in_background: boolean, timeout: integer}`
- `Agent` -> `{description, subagent_type, model, prompt, run_in_background, name: string, ...}`
- `AskUserQuestion` -> `{questions: [{question: string, header: string, multiSelect: boolean, options: [{label: string, description: string, preview: string}]}]}`
  (nested two levels — every leaf is a type token, proving the shape reducer is
  recursion-safe.)

---

## 4. v0 -> v1 schema delta

`schema.v0.draft.json` was committed **before** the extraction (its own commit)
and holds only the spec-dictated shape — no data-derived specifics. (Format
orientation had been done to understand the three harness layouts, but v0
deliberately encodes none of it.) `schema.v1.json` freezes the following
inspection-driven changes:

1. **`schema_version` bumped** `if-0-dissect-1.v0-draft` -> `if-0-dissect-1.v1`.
2. **`task_type` frozen to a discovered enum**
   (`code-modification, delegation-orchestration, execution, exploration, mixed,
   tooling-mcp, unknown, web-research`) — v0 left it a free string.
3. **`arg_shape_sample` type-vocabulary pinned** to
   `{string, integer, number, boolean, null}` at scalar leaves, with **recursive**
   object/array nesting (v0 said only "object"). Discovered by nested tools like
   `AskUserQuestion` and codex `update_plan`.
4. **`mcp__`-prefixed tool names** observed -> folded into a `tooling-mcp`
   task category (colons/`__` allowed in tool-name pattern, e.g.
   `mcp__pmcp__gateway_catalog_search`, `playwright::navigate`).
5. **Verdict record structure fixed** with concrete integer fields
   (`session_files_sampled, sessions_with_tool_calls, tool_call_count,
   distinct_tools`) + `blockers` as a **token array** (not prose) — v0 had only
   `{harness, extractable}`.
6. **Cross-harness arg normalization recorded:** claude `input` is already a
   dict; **codex `function_call.arguments` is a JSON *string*** that must be
   parsed before shaping (and `apply_patch` sends a non-JSON body -> `arg_keys`
   legitimately empty). This normalization is implemented in
   `extract_profile.py:_coerce_args` and is the main reason a v0 written before
   inspection could not have been final.
7. **Redaction moved out of JSON Schema into the validator's semantic pass** so
   it is *provably independent of schema shape* (a schema-valid row can still be
   rejected).

---

## 5. Redaction posture (`metadata_only`) — HARD GATE

`validate_profile.py` runs **two independent gates**; either failing exits
non-zero:

1. **Schema gate** — hand-rolled draft-07 subset interpreter (stdlib only, no
   `jsonschema` import), covering exactly the keywords `schema.v1.json` uses.
2. **Redaction gate** — a *semantic* pass, independent of the schema: it recurses
   every `arg_shape_sample` leaf and rejects any scalar that is **not** a type
   token in `{string, integer, number, boolean, null}`; requires `arg_keys`,
   shape keys, `tool_name`, and verdict `blockers` to match bounded identifier
   patterns.

**Proof it rejects raw values, not just bad shapes** (reproduce with the
poisoned-copy runs in the PR / verify steps):

| Poisoned copy | Schema gate | Redaction gate | Exit |
|---------------|-------------|----------------|-----:|
| `arg_shape_sample.file_path = "/home/.../secret.txt"` (schema-valid object!) | PASS | **FAIL** | 1 |
| raw value smuggled into a shape **key** | PASS | **FAIL** | 1 |
| prose body in verdict `blockers` | PASS | **FAIL** | 1 |

The first row is the load-bearing case: the row is fully schema-valid
(`arg_shape_sample` is an object), yet the redaction gate rejects it because the
leaf is a real path value, not a type token.

---

## 6. Bottom line

- claude-code tool-usage is extractable into a stable, redaction-clean,
  per-task-type profile. Both the claude-code and codex datasets validate against
  the **same** `schema.v1.json`.
- Per-task narrowing (north-star 1.4) is visible in-data (Read straddles
  execution/exploration with identical shape).
- See `feasibility_verdict.md` for the per-harness B1/B2 answer.
