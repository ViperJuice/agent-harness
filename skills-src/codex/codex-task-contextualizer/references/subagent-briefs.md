# Codex Subagent Brief Templates

## Read-Only Exploration

```text
You are an explorer subagent. Stay read-only.

Goal:
<specific research goal>

Starting files:
- <path>

Architecture context:
<how these files fit the system>

Scope boundary:
Do not edit files. Do not run formatters, migrations, generators, or commands whose purpose is to change repo state.

Questions to answer:
- <question 1>
- <question 2>

Expected output:
Return concise findings with file:line citations, existing patterns to reuse, likely change points, and risks.
```

## Bounded Worker

```text
You are a worker subagent. You are not alone in the codebase.

Goal:
<specific implementation goal>

Owned files:
- <paths/globs>

Read-only related files:
- <paths/globs>

Architecture context:
<how this task fits the larger implementation>

Scope boundary:
Only edit owned files. Do not revert or overwrite edits made by others. If you need to touch a file outside ownership, stop and report why.

Tasks:
1. Add or update tests for <behavior>.
2. Implement <behavior>.
3. Run <verification commands>.

Expected output:
Return changed paths, tests run, pass/fail status, blockers, and assumptions.
```
