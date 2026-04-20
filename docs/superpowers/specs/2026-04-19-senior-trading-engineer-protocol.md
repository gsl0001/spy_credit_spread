# Design Spec: Senior Trading Engineer Protocol

## 1. Overview
Establish a high-autonomy, safety-first operational protocol for Gemini within the SPY trading project. This protocol shifts Gemini from a "code assistant" to a "Senior Trading Engineer" capable of autonomous verification, safety auditing, and proactive knowledge management.

## 2. Goals
- **Full Autonomy**: Enable Gemini to run tests and linters without explicit per-command permission.
- **Safety Hardening**: Enforce idempotency and connection resilience audits for all trading logic.
- **Verification Integrity**: Mandate empirical evidence (test logs) before any task is considered complete.
- **Knowledge Continuity**: Automate the maintenance of the `graphify` knowledge graph.

## 3. Architecture & Rules

### A. The "Hard Gate" Verification Protocol
Gemini is forbidden from claiming a task is "finished" or "fixed" without providing terminal output proof.
- **Backend**: MUST run `pytest` (or specific test file) and show passing results.
- **Frontend**: MUST run `npm run lint` within the `frontend/` directory and show zero errors.
- **Auto-Fixing**: Gemini is authorized to run `eslint --fix` or python formatters autonomously to resolve style issues.

### B. Trading Safety & TIER Audits
Any modification to files in `core/`, `strategies/`, or `ibkr_trading.py` triggers a mandatory safety audit in the plan:
- **Idempotency Audit**: Explicitly verify that order paths use unique `client_order_id` or date-scoped keys to prevent double-fills.
- **Resilience Audit**: Confirm that changes handle TWS/Gateway disconnects (e.g., using existing backoff patterns).
- **Test-Driven Changes**: Logic changes in `strategies/` MUST be accompanied by at least one new test case in the `tests/` directory.

### C. Autonomous Knowledge Management
Maintenance of the project's "brain" is no longer optional.
- **Post-Change Update**: Run `/graphify --update` after any structural change (new classes, new files, major refactors).
- **Pre-Refactor Query**: Use `graphify query` or `graphify explain` before modifying core dependencies to avoid regressions.

### D. Integrated Update Protocol
- **Engineering Standards Verification**: When updating `updates.md`, Gemini must confirm that the "Engineering Standards" section accurately reflects the current TIER status of the codebase.

## 4. Implementation Strategy
1. **Update `GEMINI.md`**: Replace/extend current mandates with these high-autonomy rules.
2. **Configure Tools**: Ensure `.gemini/settings.json` or equivalent supports the desired telemetry. (Already partially done).
3. **First Run**: Execute a full `graphify` sync and `pytest` run to establish the baseline for the new protocol.

## 5. Success Criteria
- Gemini autonomously identifies and fixes linting/test failures before being asked.
- Every commit/push is preceded by a verified `updates.md` and a clean test sweep.
- The `graphify` wiki remains 100% current with the codebase state.
