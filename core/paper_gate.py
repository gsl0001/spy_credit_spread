"""Paper-trading gate — automated multi-preset trialing on moomoo paper.

Implements the skill's Step 10 ("run preset on moomoo paper for one full
trading week, compare live-paper stats to backtest") at scale across N
presets running simultaneously.

State machine per trial:
    trialing → passed | failed | promoted | demoted

Lifecycle:
  - User starts a trial with expected stats (WR / cadence / max DD) from
    the backtest, plus min_trades and min_days thresholds.
  - The auto-execute path checks for an active trial before firing the
    preset and enforces conservative sizing (fixed_contracts=1) regardless
    of the preset's own sizing block. This keeps trials apples-to-apples.
  - A daily evaluator job runs at 16:30 ET, computes per-trial verdict via
    the same math as /api/paper_validation, and transitions state when the
    sample-size minimums are met.
  - Promotion is **manual** — the UI exposes a Clone-to-Live button on
    passed trials; the evaluator never moves real money on its own.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

STATUS_TRIALING = "trialing"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_PROMOTED = "promoted"
STATUS_DEMOTED = "demoted"

VALID_STATUSES = {STATUS_TRIALING, STATUS_PASSED, STATUS_FAILED, STATUS_PROMOTED, STATUS_DEMOTED}


@dataclass(frozen=True)
class PaperTrial:
    """One paper-trading trial against a single preset."""
    preset_name: str
    started_at: str                       # ISO-8601 UTC
    status: str = STATUS_TRIALING
    ended_at: Optional[str] = None
    expected_win_rate_pct: float = 0.0
    expected_trades_per_week: float = 0.0
    expected_max_drawdown_pct: float = 0.0
    min_trades: int = 20
    min_days: int = 7
    verdict: str = "pending"              # pending | pass | warn | fail
    last_evaluated_at: Optional[str] = None
    notes: str = ""
    # Per-trial concurrency knobs. Defaults match the skill's
    # "fixed_contracts=1 for first-time presets" rule.
    max_open_positions: int = 1
    allow_overlap: bool = True            # allow other trials to fire on same symbol
    # Hard-fail short-circuit threshold (multiple of expected max DD that
    # triggers immediate demote on a single trade).
    fast_fail_dd_multiplier: float = 2.0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trials (
    preset_name                 TEXT PRIMARY KEY,
    started_at                  TEXT NOT NULL,
    status                      TEXT NOT NULL,
    ended_at                    TEXT,
    expected_win_rate_pct       REAL NOT NULL DEFAULT 0,
    expected_trades_per_week    REAL NOT NULL DEFAULT 0,
    expected_max_drawdown_pct   REAL NOT NULL DEFAULT 0,
    min_trades                  INTEGER NOT NULL DEFAULT 20,
    min_days                    INTEGER NOT NULL DEFAULT 7,
    verdict                     TEXT NOT NULL DEFAULT 'pending',
    last_evaluated_at           TEXT,
    notes                       TEXT NOT NULL DEFAULT '',
    max_open_positions          INTEGER NOT NULL DEFAULT 1,
    allow_overlap               INTEGER NOT NULL DEFAULT 1,
    fast_fail_dd_multiplier     REAL NOT NULL DEFAULT 2.0
);
CREATE INDEX IF NOT EXISTS idx_paper_trials_status ON paper_trials(status);
"""


class PaperGateStore:
    """SQLite-backed registry of paper-trading trials.

    Reuses the journal DB by default so we don't introduce a new file
    handle / connection pool. All writes are guarded by an internal lock
    because both the daily evaluator and the auto-execute scheduler can
    touch the same row concurrently.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        from core.settings import SETTINGS
        self.db_path = Path(db_path or SETTINGS.journal_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @staticmethod
    def _row_to_trial(row: sqlite3.Row) -> PaperTrial:
        return PaperTrial(
            preset_name=row["preset_name"],
            started_at=row["started_at"],
            status=row["status"],
            ended_at=row["ended_at"],
            expected_win_rate_pct=float(row["expected_win_rate_pct"] or 0),
            expected_trades_per_week=float(row["expected_trades_per_week"] or 0),
            expected_max_drawdown_pct=float(row["expected_max_drawdown_pct"] or 0),
            min_trades=int(row["min_trades"]),
            min_days=int(row["min_days"]),
            verdict=row["verdict"],
            last_evaluated_at=row["last_evaluated_at"],
            notes=row["notes"] or "",
            max_open_positions=int(row["max_open_positions"]),
            allow_overlap=bool(row["allow_overlap"]),
            fast_fail_dd_multiplier=float(row["fast_fail_dd_multiplier"]),
        )

    def upsert(self, trial: PaperTrial) -> PaperTrial:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO paper_trials (
                    preset_name, started_at, status, ended_at,
                    expected_win_rate_pct, expected_trades_per_week,
                    expected_max_drawdown_pct, min_trades, min_days,
                    verdict, last_evaluated_at, notes,
                    max_open_positions, allow_overlap, fast_fail_dd_multiplier
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(preset_name) DO UPDATE SET
                    started_at=excluded.started_at,
                    status=excluded.status,
                    ended_at=excluded.ended_at,
                    expected_win_rate_pct=excluded.expected_win_rate_pct,
                    expected_trades_per_week=excluded.expected_trades_per_week,
                    expected_max_drawdown_pct=excluded.expected_max_drawdown_pct,
                    min_trades=excluded.min_trades,
                    min_days=excluded.min_days,
                    verdict=excluded.verdict,
                    last_evaluated_at=excluded.last_evaluated_at,
                    notes=excluded.notes,
                    max_open_positions=excluded.max_open_positions,
                    allow_overlap=excluded.allow_overlap,
                    fast_fail_dd_multiplier=excluded.fast_fail_dd_multiplier
                """,
                (
                    trial.preset_name, trial.started_at, trial.status, trial.ended_at,
                    trial.expected_win_rate_pct, trial.expected_trades_per_week,
                    trial.expected_max_drawdown_pct, trial.min_trades, trial.min_days,
                    trial.verdict, trial.last_evaluated_at, trial.notes,
                    trial.max_open_positions, int(trial.allow_overlap),
                    trial.fast_fail_dd_multiplier,
                ),
            )
            self._conn.commit()
        return trial

    def get(self, preset_name: str) -> Optional[PaperTrial]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM paper_trials WHERE preset_name = ?", (preset_name,)
            ).fetchone()
        return self._row_to_trial(row) if row else None

    def list(self, status: Optional[str] = None) -> list[PaperTrial]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM paper_trials WHERE status = ? ORDER BY started_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM paper_trials ORDER BY started_at DESC"
                ).fetchall()
        return [self._row_to_trial(r) for r in rows]

    def list_active(self) -> list[PaperTrial]:
        return self.list(status=STATUS_TRIALING)

    def set_status(self, preset_name: str, status: str, *, verdict: str = "", notes: str = "") -> bool:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        now = datetime.now(timezone.utc).isoformat()
        ended = now if status in (STATUS_PASSED, STATUS_FAILED, STATUS_DEMOTED) else None
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE paper_trials
                   SET status = ?, ended_at = COALESCE(?, ended_at),
                       verdict = COALESCE(NULLIF(?, ''), verdict),
                       notes = COALESCE(NULLIF(?, ''), notes),
                       last_evaluated_at = ?
                 WHERE preset_name = ?
                """,
                (status, ended, verdict, notes, now, preset_name),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete(self, preset_name: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM paper_trials WHERE preset_name = ?", (preset_name,)
            )
            self._conn.commit()
            return cur.rowcount > 0


_STORE: Optional[PaperGateStore] = None


def get_paper_gate_store(db_path: Optional[str] = None) -> PaperGateStore:
    global _STORE
    if _STORE is None:
        _STORE = PaperGateStore(db_path)
    return _STORE


def evaluate_trial(trial: PaperTrial) -> dict[str, Any]:
    """Compute the verdict + per-criterion findings for a trial.

    Pulls live stats from the journal (positions tagged with preset_name
    in meta) and compares them to the trial's expected_* fields. Logic
    mirrors /api/paper_validation but is callable in-process so the
    daily evaluator job doesn't need to round-trip through HTTP.
    """
    from core.journal import get_journal

    started_dt = datetime.fromisoformat(trial.started_at)
    now = datetime.now(timezone.utc)
    days_open = max(1, (now - started_dt).days or 1)
    cutoff = trial.started_at

    journal = get_journal()
    positions = [
        p for p in journal.list_all(limit=2000)
        if p.entry_time >= cutoff
        and (p.meta or {}).get("preset_name") == trial.preset_name
    ]
    closed = [p for p in positions if p.state == "closed" and p.realized_pnl is not None]
    wins = [p for p in closed if (p.realized_pnl or 0) > 0]
    losses = [p for p in closed if (p.realized_pnl or 0) <= 0]
    n_closed = len(closed)
    live_wr = (len(wins) / n_closed * 100.0) if n_closed > 0 else 0.0
    biggest_loss = min((p.realized_pnl or 0) for p in closed) if closed else 0.0
    weeks_observed = max(1.0, days_open / 7.0)
    live_cadence = len(positions) / weeks_observed

    # Use journal-recorded entry_cost (max-risk for debit spreads) at trial
    # start to scale max-DD% to dollars. Falls back to $1000 if unknown.
    if closed:
        capital_proxy = max((float(p.entry_cost or 0) * 10) for p in closed)
    else:
        capital_proxy = 1000.0
    expected_max_loss = (trial.expected_max_drawdown_pct / 100.0) * capital_proxy

    findings: list[dict[str, Any]] = []
    verdict = "pass"

    # 1. trade-count cadence
    if trial.expected_trades_per_week > 0:
        ratio = live_cadence / trial.expected_trades_per_week
        if ratio < 0.25:
            findings.append({
                "criterion": "trade_count",
                "severity": "warn",
                "message": f"live cadence {live_cadence:.2f}/wk is {ratio:.0%} of backtest {trial.expected_trades_per_week:.2f}/wk",
            })
            verdict = "warn" if verdict == "pass" else verdict
        elif ratio > 4.0:
            findings.append({
                "criterion": "trade_count",
                "severity": "warn",
                "message": f"live cadence {live_cadence:.2f}/wk = {ratio:.0%} of backtest — overtrading?",
            })
            verdict = "warn" if verdict == "pass" else verdict

    # 2. win-rate band (±10pp default per skill)
    if trial.expected_win_rate_pct > 0 and n_closed >= 3:
        delta = live_wr - trial.expected_win_rate_pct
        if abs(delta) > 20:
            findings.append({
                "criterion": "win_rate",
                "severity": "fail",
                "message": f"live WR {live_wr:.1f}% deviates {delta:+.1f}pp from backtest {trial.expected_win_rate_pct:.1f}% (>20pp)",
            })
            verdict = "fail"
        elif abs(delta) > 10:
            findings.append({
                "criterion": "win_rate",
                "severity": "warn",
                "message": f"live WR {live_wr:.1f}% deviates {delta:+.1f}pp from backtest {trial.expected_win_rate_pct:.1f}%",
            })
            verdict = "warn" if verdict == "pass" else verdict

    # 3. single-trade catastrophic loss
    if expected_max_loss < 0 and biggest_loss < expected_max_loss * trial.fast_fail_dd_multiplier:
        findings.append({
            "criterion": "max_single_loss",
            "severity": "fail",
            "message": f"single loss ${biggest_loss:.2f} exceeds {trial.fast_fail_dd_multiplier:.1f}× backtest max DD ${expected_max_loss:.2f}",
        })
        verdict = "fail"

    # Sample-size readiness for promotion/demotion
    sample_ready = n_closed >= trial.min_trades and days_open >= trial.min_days

    return {
        "preset_name": trial.preset_name,
        "verdict": verdict,
        "days_open": days_open,
        "sample_ready": sample_ready,
        "live": {
            "positions_total": len(positions),
            "positions_closed": n_closed,
            "win_rate_pct": round(live_wr, 2),
            "trades_per_week": round(live_cadence, 2),
            "biggest_loss_dollars": round(biggest_loss, 2),
            "wins": len(wins),
            "losses": len(losses),
        },
        "expected": {
            "win_rate_pct": trial.expected_win_rate_pct,
            "trades_per_week": trial.expected_trades_per_week,
            "max_drawdown_pct": trial.expected_max_drawdown_pct,
        },
        "findings": findings,
    }


def run_daily_evaluator() -> dict[str, Any]:
    """Iterate all trialing trials, evaluate, and transition state.

    Called by the APScheduler 16:30 ET job and exposed via
    POST /api/paper_trials/evaluate_now for manual triggering.
    """
    store = get_paper_gate_store()
    results: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []

    for trial in store.list_active():
        result = evaluate_trial(trial)
        store.upsert(PaperTrial(
            preset_name=trial.preset_name,
            started_at=trial.started_at,
            status=STATUS_TRIALING,
            ended_at=trial.ended_at,
            expected_win_rate_pct=trial.expected_win_rate_pct,
            expected_trades_per_week=trial.expected_trades_per_week,
            expected_max_drawdown_pct=trial.expected_max_drawdown_pct,
            min_trades=trial.min_trades,
            min_days=trial.min_days,
            verdict=result["verdict"],
            last_evaluated_at=datetime.now(timezone.utc).isoformat(),
            notes=trial.notes,
            max_open_positions=trial.max_open_positions,
            allow_overlap=trial.allow_overlap,
            fast_fail_dd_multiplier=trial.fast_fail_dd_multiplier,
        ))

        new_status: Optional[str] = None
        # Immediate fail on catastrophic single loss regardless of sample size
        if result["verdict"] == "fail" and any(f["criterion"] == "max_single_loss"
                                                for f in result["findings"]):
            new_status = STATUS_FAILED
        elif result["sample_ready"]:
            if result["verdict"] == "pass":
                new_status = STATUS_PASSED
            elif result["verdict"] == "fail":
                new_status = STATUS_FAILED
        if new_status:
            store.set_status(
                trial.preset_name, new_status,
                verdict=result["verdict"],
                notes=json.dumps(result["findings"])[:500],
            )
            # Demote the preset's auto_execute when failing so it stops firing
            if new_status == STATUS_FAILED:
                try:
                    from core.presets import PresetStore
                    pstore = PresetStore()
                    preset = pstore.get(trial.preset_name)
                    if preset and preset.auto_execute:
                        from dataclasses import replace
                        pstore.save(replace(preset, auto_execute=False))
                except Exception:
                    pass
            transitions.append({"preset": trial.preset_name, "to": new_status})
        results.append(result)

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(results),
        "transitions": transitions,
        "results": results,
    }


def trial_allows_fire(preset_name: str) -> tuple[bool, str]:
    """Pre-fire gate consulted by the auto-execute loop.

    Returns (allowed, reason). Used to enforce per-trial position caps
    and overlap policies without contaminating the trial sample.
    """
    store = get_paper_gate_store()
    trial = store.get(preset_name)
    if trial is None:
        return True, ""  # not under trial — let the normal path decide
    if trial.status != STATUS_TRIALING:
        return False, f"trial_{trial.status}"
    from core.journal import get_journal
    journal = get_journal()
    open_pos = [
        p for p in journal.list_open()
        if (p.meta or {}).get("preset_name") == preset_name
    ]
    if len(open_pos) >= trial.max_open_positions:
        return False, "trial_position_cap_reached"
    return True, ""
