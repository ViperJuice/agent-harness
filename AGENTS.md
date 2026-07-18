# AGENTS.md

Agent guidance for contributors working in this repository.

This is the anchor for agent-facing conventions in `agent-harness`. Keep it
tight and self-contained.

## Referencing issues & PRs (multi-repo)

`agent-harness` is one node in a multi-repo fleet, so a bare `#123` is
ambiguous — it could mean an issue or PR in any repo. Always qualify the number
with its repository:

- Write `agent-harness#130`, or the fully-qualified `Consiliency/agent-harness#130`.
- Never write a lone `#130`.

This applies **everywhere a number appears**: chat and status updates, commit
messages, PR and issue bodies, handoffs, and closeout reports. When you
reference an issue or PR in another repo, qualify it with that repo
(`portal#42`, `consiliency-contract#7`), never a bare `#42`.
