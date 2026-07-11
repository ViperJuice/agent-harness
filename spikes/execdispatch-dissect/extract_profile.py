#!/usr/bin/env python3
"""IF-0-DISSECT-1 tool-usage profile extractor (research spike, not production code).

Reads real harness session transcripts and emits a METADATA-ONLY tool-usage
profile: per-(task_type, tool_name) rows carrying {call_count, arg_keys,
arg_shape_sample, frequency} plus a top-level per-harness feasibility verdict
record. NEVER emits raw argument values, file contents, or prompt/message bodies
— only tool names, argument KEYS, argument SHAPE/type, and counts/frequencies.

Supported harnesses:
  claude : ~/.claude/projects/**/*.jsonl
           line{type:"assistant"}.message.content[]{type:"tool_use", name, input:{...dict...}}
  codex  : ~/.codex/archived_sessions/rollout-*.jsonl
           line{type:"response_item"}.payload{type:"function_call", name, arguments:"<JSON string>"}
           and {type:"custom_tool_call"/"web_search_call", ...}

Usage:
  extract_profile.py --harness claude --out profile.json [--sample N] [--seed S]
  extract_profile.py --harness codex  --out profile.json [--sample N] [--seed S]
  extract_profile.py --harness claude --files a.jsonl b.jsonl --out profile.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
from collections import Counter, defaultdict

# --- redaction-safe leaf type vocabulary (must match schema.v1.json + validator) ---
SCALAR_VOCAB = {"string", "integer", "number", "boolean", "null"}

# --- task_type classification: derived from TOOL-MIX only (redaction-clean; no
#     project-dir paths, no prompt bodies feed the label). Documented in report.md. ---
TASK_CATEGORY_TOOLS = {
    "code-modification": {
        "Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch",
        "create_file", "str_replace_editor", "edit_file",
    },
    "exploration": {
        "Read", "Grep", "Glob", "LS", "read_file", "list_dir", "grep",
        "search_code", "symbol_lookup", "codebase_search",
    },
    "execution": {
        "Bash", "BashOutput", "KillBash", "exec_command", "shell",
        "run_terminal_cmd", "local_shell",
    },
    "delegation-orchestration": {
        "Task", "Agent", "SendMessage", "TeamCreate", "TaskStop",
    },
    "web-research": {
        "WebFetch", "WebSearch", "web_search", "web_search_call", "browser",
    },
}
_TOOL_TO_CATEGORY = {t: cat for cat, tools in TASK_CATEGORY_TOOLS.items() for t in tools}
# tool names matching this prefix are MCP tools; categorized as 'tooling-mcp' unless mapped
MCP_PREFIX = "mcp__"
TASK_TYPE_ENUM = sorted(set(TASK_CATEGORY_TOOLS) | {"tooling-mcp", "mixed", "unknown"})
DOMINANCE_THRESHOLD = 0.40  # a single category must own >=40% of a session's calls to name it


def shape_of(value):
    """Map a value to its SHAPE (types only). Scalars -> vocab word; dict -> nested
    shape object; list -> single-element array of element shapes. NEVER returns a
    raw value — the redaction guarantee lives here."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return {str(k): shape_of(v) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return []
        # union element shapes down to a representative; keep it bounded
        return [shape_of(value[0])]
    return "string"  # unknown/bytes -> conservative scalar


def merge_shape(acc, new):
    """Union two shapes (used when the same key appears with varying structure)."""
    if acc is None:
        return new
    if isinstance(acc, dict) and isinstance(new, dict):
        out = dict(acc)
        for k, v in new.items():
            out[k] = merge_shape(out.get(k), v)
        return out
    if isinstance(acc, list) and isinstance(new, list):
        if acc and new:
            return [merge_shape(acc[0], new[0])]
        return acc or new
    return acc  # first-seen scalar wins; conflicts are rare and both are vocab words


def _coerce_args(raw):
    """Return a dict of arguments from a tool call, parsing codex's JSON-string form.
    Returns {} for absent/unparseable/non-dict args."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def iter_calls_claude(path):
    """Yield (tool_name, args_dict) from a claude-code transcript."""
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("type") != "assistant":
                continue
            content = (obj.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name")
                    if name:
                        yield str(name), _coerce_args(block.get("input"))


def iter_calls_codex(path):
    """Yield (tool_name, args_dict) from a codex rollout transcript."""
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("type") != "response_item":
                continue
            p = obj.get("payload") or {}
            pt = p.get("type")
            if pt == "function_call":
                name = p.get("name")
                if name:
                    yield str(name), _coerce_args(p.get("arguments"))
            elif pt == "custom_tool_call":
                name = p.get("name")
                if name:
                    yield str(name), _coerce_args(p.get("input"))
            elif pt == "web_search_call":
                yield "web_search_call", {}


ITERATORS = {"claude": iter_calls_claude, "codex": iter_calls_codex}


def classify_task_type(tool_counter):
    """Coarse task_type from a session's TOOL-MIX (redaction-clean). Returns an
    enum value. Dominant category = the category holding >=DOMINANCE_THRESHOLD of
    calls; else 'mixed'. Empty -> 'unknown'."""
    total = sum(tool_counter.values())
    if total == 0:
        return "unknown"
    cat_counts = Counter()
    for tool, n in tool_counter.items():
        cat = _TOOL_TO_CATEGORY.get(tool)
        if cat is None:
            cat = "tooling-mcp" if tool.startswith(MCP_PREFIX) else None
        if cat is not None:
            cat_counts[cat] += n
    if not cat_counts:
        return "mixed"
    top_cat, top_n = cat_counts.most_common(1)[0]
    if top_n / total >= DOMINANCE_THRESHOLD:
        return top_cat
    return "mixed"


def default_paths(harness):
    if harness == "claude":
        return glob.glob(os.path.expanduser("~/.claude/projects/**/*.jsonl"), recursive=True)
    if harness == "codex":
        return glob.glob(os.path.expanduser("~/.codex/archived_sessions/rollout-*.jsonl"))
    return []


def sample_paths(paths, sample, seed):
    """Stride/randomize across the file list for representativeness (not head-N)."""
    paths = sorted(paths)
    if sample and sample < len(paths):
        rnd = random.Random(seed)
        return sorted(rnd.sample(paths, sample))
    return paths


def build_profile(harness, files, sample, seed):
    it = ITERATORS[harness]
    files = sample_paths(files, sample, seed)
    # (task_type, tool_name) -> aggregate
    agg = defaultdict(lambda: {"call_count": 0, "arg_keys": set(), "shape": None})
    sessions_parsed = 0
    sessions_with_calls = 0
    total_calls = 0
    per_session = []  # (task_type, [(tool,args)...]) to attribute calls after classification

    for path in files:
        try:
            calls = list(it(path))
        except OSError:
            continue
        sessions_parsed += 1
        if not calls:
            continue
        sessions_with_calls += 1
        counter = Counter(name for name, _ in calls)
        task_type = classify_task_type(counter)
        per_session.append((task_type, calls))
        total_calls += len(calls)

    task_type_totals = Counter()
    for task_type, calls in per_session:
        task_type_totals[task_type] += len(calls)

    for task_type, calls in per_session:
        for name, args in calls:
            cell = agg[(task_type, name)]
            cell["call_count"] += 1
            cell["arg_keys"].update(str(k) for k in args.keys())
            cell["shape"] = merge_shape(cell["shape"], shape_of(args))

    rows = []
    for (task_type, tool_name), cell in sorted(agg.items(), key=lambda kv: (-kv[1]["call_count"], kv[0])):
        denom = task_type_totals[task_type] or 1
        shape = cell["shape"]
        if not isinstance(shape, dict):
            shape = {}
        rows.append({
            "task_type": task_type,
            "tool_name": tool_name,
            "call_count": cell["call_count"],
            "arg_keys": sorted(cell["arg_keys"]),
            "arg_shape_sample": shape,
            "frequency": round(cell["call_count"] / denom, 6),
        })

    verdict = {
        "harness": harness,
        "extractable": sessions_with_calls > 0,
        "session_files_sampled": len(files),
        "sessions_with_tool_calls": sessions_with_calls,
        "tool_call_count": total_calls,
        "distinct_tools": len({tn for _, tn in agg.keys()}),
        "blockers": [] if sessions_with_calls > 0 else ["no_tool_calls_found"],
    }

    return {
        "schema_version": "if-0-dissect-1.v1",
        "generated_by": "spikes/execdispatch-dissect/extract_profile.py",
        "verdicts": [verdict],
        "rows": rows,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--harness", required=True, choices=sorted(ITERATORS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--files", nargs="*", help="explicit transcript paths (overrides defaults)")
    ap.add_argument("--sample", type=int, default=0, help="sample N files (0 = all)")
    ap.add_argument("--seed", type=int, default=1729)
    args = ap.parse_args(argv)

    files = args.files or default_paths(args.harness)
    if not files:
        print(f"no {args.harness} transcripts found", file=sys.stderr)
        return 2
    profile = build_profile(args.harness, files, args.sample, args.seed)
    with open(args.out, "w") as fh:
        json.dump(profile, fh, indent=2, sort_keys=False)
        fh.write("\n")
    v = profile["verdicts"][0]
    print(f"{args.harness}: sampled {v['session_files_sampled']} files, "
          f"{v['sessions_with_tool_calls']} with tool calls, "
          f"{v['tool_call_count']} calls, {v['distinct_tools']} distinct tools, "
          f"{len(profile['rows'])} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
