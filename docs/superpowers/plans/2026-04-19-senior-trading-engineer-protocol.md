# Senior Trading Engineer Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formally integrate the high-autonomy "Senior Trading Engineer" rules into the project's foundation (`GEMINI.md`).

**Architecture:** Extend existing mandates with hard-gate verification, trading safety audits, and automated knowledge graph maintenance.

**Tech Stack:** Markdown, Gemini CLI, Pytest, ESLint, Graphify.

---

### Task 1: Update GEMINI.md Mandates

**Files:**
- Modify: `GEMINI.md`

- [ ] **Step 1: Replace Mandates section with updated protocol**

```markdown
# Gemini Project Instructions

These instructions are foundational for all interactions in this workspace.

## Mandates

### 1. The "Hard Gate" Verification
- **Empirical Evidence Required**: You MUST NOT claim a task is "finished", "fixed", or "implemented" without providing terminal output proof.
- **Verification Commands**: 
  - Backend: Run `pytest` (or specific test file) and show passing results.
  - Frontend: Run `npm run lint` in `frontend/` and show zero errors.
- **Autonomous Fixing**: You are authorized to run `eslint --fix` or Python formatters (like `black` or `ruff`) autonomously to maintain standards.

### 2. Trading Safety & TIER Audits
Any modification to `core/`, `strategies/`, or `ibkr_trading.py` requires a safety audit in your plan:
- **Idempotency**: Verify that order paths use unique `client_order_id` or date-scoped keys.
- **Resilience**: Confirm changes handle TWS/Gateway disconnects using established backoff patterns.
- **Testing**: Logic changes in `strategies/` MUST be accompanied by a new test case in `tests/`.

### 3. Knowledge Graph Maintenance
- **Update Cycle**: Run `/graphify --update` after any structural change (new files, new classes, or major refactors).
- **Architectural Awareness**: Use `graphify query` or `graphify explain` to map dependencies before making significant edits to core logic.

### 4. Continuous Documentation (`updates.md`)
- **Mandatory Log**: Update `updates.md` at the end of every task, documenting improvements, safety audits, and testing results.
- **Git Protocol**: Ensure `updates.md` is updated and verified before any git push. Propose a clear commit message summarizing all changes.

### 5. CLI Presentation (Session Stats Bar)
- **Footer Mandate**: Every response MUST conclude with a horizontal Session Stats Bar.
- **Format**: `[ MODEL: {model} | CONTEXT: {used}/{limit} ({pct}%) | TOKENS: {total} | AGENTS: {count} ]`
- Ensure this bar is visually separated from the main content by a single newline.

## Engineering Standards
- Maintain visual/functional integrity.
- Prioritize idempotency, safety, and connection resilience (TIER requirements).
```

- [ ] **Step 2: Commit changes**

```bash
git add GEMINI.md
git commit -m "docs: implement Senior Trading Engineer protocol in GEMINI.md"
```

### Task 2: Baseline Verification & Sync

**Files:**
- None (Command execution)

- [ ] **Step 1: Run baseline test suite**

Run: `pytest`
Expected: View current passing/failing state to establish baseline for new protocol.

- [ ] **Step 2: Run baseline lint check**

Run: `cd frontend; npm run lint`
Expected: View current lint state.

- [ ] **Step 3: Update Knowledge Graph**

Run: `/graphify --update`
Expected: Knowledge graph synced with current codebase.

### Task 3: Documentation Update

**Files:**
- Modify: `updates.md`

- [ ] **Step 1: Document the Protocol implementation**

Add to `updates.md` under a new section:
```markdown
## 📜 Senior Trading Engineer Protocol (April 19, 2026)
- **High-Autonomy Mandate**: Formally integrated "Hard Gate" verification and "Trading Safety Audits" into `GEMINI.md`.
- **Automated Verification**: Gemini is now mandated to provide empirical test/lint evidence for all task completions.
- **Safety Hardening**: Established mandatory idempotency audits for all order execution logic.
```

- [ ] **Step 2: Commit update**

```bash
git add updates.md
git commit -m "docs: log Senior Trading Engineer protocol implementation in updates.md"
```
