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
