---
name: task-contextualizer
description: "Harness Code subagent briefing guide. Use before creating any Task tool call, especially for Explore or general-purpose subagents. Provides templates for task prompts that include necessary file paths, architecture context, and scope boundaries."
---

# Task Contextualizer

## Runtime State

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, helper roots, repo-local handoff roots, and harness-specific reflection roots.

For reflections, handoffs, and latest handoff pointers, follow `<harness>-config/shared/runtime-state.md`. This repo/branch/run-isolated contract supersedes any older flat closeout examples retained for historical context in this skill.

Subagents do not inherit parent context. Brief every Task call with specific file paths, architecture, and scope so the subagent can start work immediately instead of rediscovering the project.

## Mandatory Checklist

Before EVERY Task tool call, verify:

- [ ] **File paths**: Listed the specific files the subagent needs to read or modify
- [ ] **Architecture context**: Explained how the relevant module fits into the larger system (1-2 sentences)
- [ ] **Scope boundary**: Told the subagent what NOT to touch
- [ ] **Expected output**: Specified what the subagent should produce (file changes, report, answer)
- [ ] **Related files**: Mentioned test files, config files, type definitions

## Bad vs Good

**BAD** (forces subagent to discover everything):
```
Fix the TypeScript error in the chat service
```

**GOOD** (subagent can start working immediately):
```
Fix the TypeScript error in /src/services/chatService.ts.

Error: "Property 'messageId' does not exist on type 'ChatEvent'" at line 142.

Related files:
- /src/types/chat.ts — ChatEvent and ChatMessage type definitions
- /src/services/apiClient.ts — base service class that chatService extends
- /tests/services/chatService.test.ts — existing tests

Architecture: chatService extends BaseApiService and handles WebSocket
messages. ChatEvent is the raw event type; ChatMessage is the processed type.

Scope: Only modify chatService.ts and chat.ts. Do not change apiClient.ts.
```

## Templates by Task Type

### Bug Fix
Include: file path + exact error message + related type/config files + test file

### New Feature
Include: example files showing the pattern to follow + target directory + naming/style conventions

### Research / Explore
Include: known file paths to start from + specific questions to answer + what you already know (so the agent doesn't re-discover it)

### Refactor
Include: all files to change + the target pattern + constraints on what must stay the same

## Key Insight for Explore Subagents

Explore agents are read-only — they can't edit files. But they spend the MOST on context discovery because they're often asked broad questions. Always give them:
1. Specific file paths to start from (not "find where X is")
2. Specific questions to answer (not "understand module X")
3. What you already know (so they don't re-discover it)

## Resources

### references/
Full copy-paste templates with placeholders for each task type. See `references/task-templates.md` for ready-to-use templates.
