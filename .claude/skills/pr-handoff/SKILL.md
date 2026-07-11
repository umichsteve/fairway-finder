---
name: pr-handoff
description: Write or consume a HANDOFF.md work order at the repo root. Use when asked to "hand off", "write a handoff", "prepare a handoff", or automatically at session start when HANDOFF.md exists at the repo root.
---

# PR Handoff

## Consuming a handoff (session start)

1. If `HANDOFF.md` exists at the repo root, read it before doing anything else.
2. Treat **Done when** as the acceptance criteria and **Non-goals** as hard boundaries — do not touch anything listed there.
3. Implement the Task section.
4. Run every step in the Verify section and confirm it passes.
5. Delete `HANDOFF.md` as part of the final commit.

## Writing a handoff (handing work off)

Create `HANDOFF.md` at the repo root in exactly this format:

```markdown
# HANDOFF

- Project: <repo name>
- Mode: PLAN | BUILD | FIX | REVIEW
- Context: <branch, deployment URL, relevant files — or "n/a">
- Done when: <one-line acceptance criteria>
- Non-goals: <what not to change / touch / add>

## Task

<specific, self-contained instructions>

## Verify

<commands to run or URLs to check that prove it works>
```

Rules: one handoff at a time; keep Task self-contained (assume the reader has zero chat context); cloud-only verification (preview deploys, not local dev servers).
