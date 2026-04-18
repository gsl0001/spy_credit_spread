"""SQLite-backed trade journal.

Survives restarts. Every position / order / fill touches this file first,
then the live broker. On server boot, the monitor loop asks the journal
for open positions so it can resume lifecycle management without losing
state.

Design choices:
    - Frozen dataclasses for entities. Mutations return new objects.
    - Single file DB (data/trades.db by default).
    - `contextlib.contextmanager` for connections; callers never see raw
      sqlite3.Connection objects.
    - JSON columns for `legs` and free-form metadata so we don't have to
      migrate the schema every time a new topology lands.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Position:
    id: str
    symbol: str
    topology: str
    direction: str
    contracts: int
    entry_cost: float           # total debit paid (or credit received, negative)
    entry_time: str             # ISO-8601 UTC
    expiry: str                 # YYYY-MM-DD
    legs: tuple[dict, ...]      # tuple of leg dicts to stay hashable
    state: str = "pending"      # pending | open | closing | closed | cancelled
    exit_cost: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None
    realized_pnl: Optional[float] = None
    high_water_mark: Optional[float] = None
    broker: str = "ibkr"
    account: str = ""
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Order:
    id: str
    position_id: str
    broker: str                 # ibkr | alpaca
    broker_order_id: Optional[str]
    side: str                   # BUY | SELL
    limit_price: Optional[float]
    status: str                 # submitted | filled | partial | cancelled | rejected
    submitted_at: str
    filled_at: Optional[str] = None
    fill_price: Optional[float] = None
    commission: float = 0.0
    kind: str = "entry"         # entry | exit | cancel
    idempotency_key: Optional[str] = None


@dataclass(frozen=True)
class Fill:
    id: Optional[int]
    order_id: str
    qty: int
    price: float
    time: str
    exec_id: Optional[str] = None
    commission: float = 0.0


# ── Schema ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id              TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    topology        TEXT NOT NULL,
    direction       TEXT NOT NULL,
    contracts       INTEGER NOT NULL,
    entry_cost      REAL NOT NULL,
    entry_time      TEXT NOT NULL,
    expiry          TEXT NOT NULL,
    state           TEXT NOT NULL,
    exit_cost       REAL,
    exit_time       TEXT,
    exit_reason     TEXT,
    realized_pnl    REAL,
    high_water_mark REAL,
    broker          TEXT NOT NULL DEFAULT 'ibkr',
    account         TEXT DEFAULT '',
    legs_json       TEXT NOT NULL,
    meta_json       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS orders (
    id               TEXT PRIMARY KEY,
    position_id      TEXT,
    broker           TEXT NOT NULL,
    broker_order_id  TEXT,
    side             TEXT NOT NULL,
    limit_price      REAL,
    status           TEXT NOT NULL,
    submitted_at     TEXT NOT NULL,
    filled_at        TEXT,
    fill_price       REAL,
    commission       REAL DEFAULT 0,
    kind             TEXT NOT NULL DEFAULT 'entry',
    idempotency_key  TEXT UNIQUE,
    FOREIGN KEY(position_id) REFERENCES positions(id)
);

CREATE TABLE IF NOT EXISTS fills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    price       REAL NOT NULL,
    time        TEXT NOT NULL,
    exec_id     TEXT,
    commission  REAL DEFAULT 0,
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    date        TEXT PRIMARY KEY,
    realized    REAL NOT NULL DEFAULT 0,
    trades      INTEGER NOT NULL DEFAULT 0,
    win_count   INTEGER NOT NULL DEFAULT 0,
    loss_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    time        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    subject     TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

-- I11: persist scanner-run history across restarts.
CREATE TABLE IF NOT EXISTS scanner_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    time        TEXT NOT NULL,
    signal      INTEGER NOT NULL DEFAULT 0,    -- boolean
    price       REAL,
    rsi         REAL,
    msg         TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_positions_state    ON positions(state);
CREATE INDEX IF NOT EXISTS idx_orders_position    ON orders(position_id);
CREATE INDEX IF NOT EXISTS idx_fills_order        ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_events_time        ON events(time);
CREATE INDEX IF NOT EXISTS idx_scanner_logs_time  ON scanner_logs(time);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_str() -> str:
    return date.today().isoformat()


# ── Journal ────────────────────────────────────────────────────────────────

class Journal:
    """Thread-safe SQLite journal. One instance per process is sufficient.

    ``check_same_thread=False`` + an RLock lets APScheduler's worker thread
    and the FastAPI request thread share the same connection safely.
    """

    def __init__(self, db_path: str = "data/trades.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage txns manually
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA)

    # ── low-level helpers ──────────────────────────────────────────────────

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # ── positions ──────────────────────────────────────────────────────────

    def open_position(self, pos: Position) -> str:
        """Insert a new position. Returns the position id."""
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO positions (
                    id, symbol, topology, direction, contracts,
                    entry_cost, entry_time, expiry, state,
                    exit_cost, exit_time, exit_reason, realized_pnl,
                    high_water_mark, broker, account, legs_json, meta_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pos.id, pos.symbol, pos.topology, pos.direction, pos.contracts,
                    pos.entry_cost, pos.entry_time, pos.expiry, pos.state,
                    pos.exit_cost, pos.exit_time, pos.exit_reason, pos.realized_pnl,
                    pos.high_water_mark, pos.broker, pos.account,
                    json.dumps(list(pos.legs)), json.dumps(pos.meta),
                ),
            )
        return pos.id

    def update_position(self, pos_id: str, **changes: Any) -> None:
        """Partial update. Only the fields provided in `changes` are written."""
        if not changes:
            return
        allowed = {
            "state", "exit_cost", "exit_time", "exit_reason", "realized_pnl",
            "high_water_mark", "entry_cost", "contracts", "expiry",
        }
        bad = set(changes) - allowed
        if bad:
            raise ValueError(f"Unknown position fields: {bad}")
        cols = ", ".join(f"{k} = ?" for k in changes)
        vals = list(changes.values()) + [pos_id]
        with self._tx() as c:
            c.execute(f"UPDATE positions SET {cols} WHERE id = ?", vals)

    def close_position(
        self,
        pos_id: str,
        *,
        exit_cost: float,
        reason: str,
        realized_pnl: float,
        time: Optional[str] = None,
    ) -> None:
        t = time or _utc_now_iso()
        with self._tx() as c:
            c.execute(
                """
                UPDATE positions
                   SET state       = 'closed',
                       exit_cost   = ?,
                       exit_reason = ?,
                       realized_pnl= ?,
                       exit_time   = ?
                 WHERE id = ?
                """,
                (exit_cost, reason, realized_pnl, t, pos_id),
            )
            # Bump daily P&L rollup
            today = _today_str()
            win = 1 if realized_pnl > 0 else 0
            loss = 1 if realized_pnl <= 0 else 0
            c.execute(
                """
                INSERT INTO daily_pnl (date, realized, trades, win_count, loss_count)
                     VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    realized   = realized   + excluded.realized,
                    trades     = trades     + 1,
                    win_count  = win_count  + excluded.win_count,
                    loss_count = loss_count + excluded.loss_count
                """,
                (today, realized_pnl, win, loss),
            )

    def get_position(self, pos_id: str) -> Optional[Position]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM positions WHERE id = ?", (pos_id,)
            ).fetchone()
        return _row_to_position(row) if row else None

    def list_open(self) -> list[Position]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM positions WHERE state IN ('pending','open','closing') "
                "ORDER BY entry_time"
            ).fetchall()
        return [_row_to_position(r) for r in rows]

    def list_all(self, limit: int = 200) -> list[Position]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM positions ORDER BY entry_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_position(r) for r in rows]

    # ── orders ─────────────────────────────────────────────────────────────

    def record_order(self, order: Order) -> None:
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO orders (
                    id, position_id, broker, broker_order_id, side, limit_price,
                    status, submitted_at, filled_at, fill_price, commission,
                    kind, idempotency_key
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    filled_at = excluded.filled_at,
                    fill_price = excluded.fill_price,
                    commission = excluded.commission,
                    broker_order_id = COALESCE(excluded.broker_order_id, orders.broker_order_id)
                """,
                (
                    order.id, order.position_id, order.broker, order.broker_order_id,
                    order.side, order.limit_price, order.status, order.submitted_at,
                    order.filled_at, order.fill_price, order.commission,
                    order.kind, order.idempotency_key,
                ),
            )

    def get_order_by_idempotency(self, key: str) -> Optional[Order]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM orders WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return _row_to_order(row) if row else None

    def list_orders_for_position(self, pos_id: str) -> list[Order]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM orders WHERE position_id = ? ORDER BY submitted_at",
                (pos_id,),
            ).fetchall()
        return [_row_to_order(r) for r in rows]

    def list_orders_by_status(
        self,
        statuses: tuple[str, ...] = ("submitted", "partial"),
        kind: Optional[str] = None,
    ) -> list[Order]:
        """List open orders by status (+ optional kind filter)."""
        placeholders = ",".join("?" * len(statuses))
        params: list[Any] = list(statuses)
        sql = f"SELECT * FROM orders WHERE status IN ({placeholders})"
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY submitted_at"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_order(r) for r in rows]

    # ── fills ──────────────────────────────────────────────────────────────

    def record_fill(self, fill: Fill) -> int:
        with self._tx() as c:
            cur = c.execute(
                """
                INSERT INTO fills (order_id, qty, price, time, exec_id, commission)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    fill.order_id, fill.qty, fill.price, fill.time,
                    fill.exec_id, fill.commission,
                ),
            )
            return int(cur.lastrowid or 0)

    def list_fills(self, order_id: str) -> list[Fill]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM fills WHERE order_id = ? ORDER BY time",
                (order_id,),
            ).fetchall()
        return [
            Fill(
                id=r["id"], order_id=r["order_id"], qty=r["qty"], price=r["price"],
                time=r["time"], exec_id=r["exec_id"], commission=r["commission"],
            ) for r in rows
        ]

    # ── daily rollups ──────────────────────────────────────────────────────

    def today_realized_pnl(self) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT realized FROM daily_pnl WHERE date = ?", (_today_str(),)
            ).fetchone()
        return float(row["realized"]) if row else 0.0

    def today_trade_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT trades FROM daily_pnl WHERE date = ?", (_today_str(),)
            ).fetchone()
        return int(row["trades"]) if row else 0

    def history_pnl(self, days: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT date, realized, trades, win_count, loss_count "
                "FROM daily_pnl ORDER BY date DESC LIMIT ?",
                (days,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── audit trail ────────────────────────────────────────────────────────

    def log_event(self, kind: str, subject: str = "", payload: Optional[dict] = None) -> None:
        # Sanitise payload before storage: IEEE-754 NaN/Inf are truthy in Python
        # but json.dumps() serialises them as bare `NaN`/`Infinity` tokens which
        # violate RFC 8259 and cause json.loads() to fail on read-back.
        import math as _math

        def _sanitise(obj):
            if isinstance(obj, dict):
                return {k: _sanitise(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_sanitise(v) for v in obj]
            if isinstance(obj, float) and (_math.isnan(obj) or _math.isinf(obj)):
                return None
            return obj

        safe_payload = _sanitise(payload or {})
        with self._tx() as c:
            c.execute(
                "INSERT INTO events (time, kind, subject, payload_json) VALUES (?,?,?,?)",
                (_utc_now_iso(), kind, subject, json.dumps(safe_payload)),
            )

    def recent_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT time, kind, subject, payload_json FROM events "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except (json.JSONDecodeError, ValueError):
                # Corrupted record (e.g. stored NaN before fix) — surface as-is.
                payload = {"_raw": r["payload_json"], "_parse_error": "invalid JSON"}
            out.append({
                "time": r["time"], "kind": r["kind"], "subject": r["subject"],
                "payload": payload,
            })
        return out

    # ── scanner logs (I11) ─────────────────────────────────────────────────

    def record_scan_log(
        self,
        time: str,
        signal: bool,
        price: Optional[float],
        rsi: Optional[float],
        msg: str = "",
        details: Optional[dict] = None,
    ) -> int:
        """Persist a single scanner-run entry for post-hoc review."""
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO scanner_logs (time, signal, price, rsi, msg, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    time,
                    1 if signal else 0,
                    price,
                    rsi,
                    msg,
                    json.dumps(details or {}),
                ),
            )
            return int(cur.lastrowid or 0)

    def list_scan_logs(self, limit: int = 50) -> list[dict]:
        """Return the most recent N scanner entries, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT time, signal, price, rsi, msg, details_json "
                "FROM scanner_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "time": r["time"],
                "signal": bool(r["signal"]),
                "price": r["price"],
                "rsi": r["rsi"],
                "msg": r["msg"],
                "details": json.loads(r["details_json"] or "{}"),
            }
            for r in rows
        ]


# ── Row mappers ────────────────────────────────────────────────────────────

def _row_to_position(r: sqlite3.Row) -> Position:
    return Position(
        id=r["id"], symbol=r["symbol"], topology=r["topology"],
        direction=r["direction"], contracts=int(r["contracts"]),
        entry_cost=float(r["entry_cost"]), entry_time=r["entry_time"],
        expiry=r["expiry"], state=r["state"],
        exit_cost=r["exit_cost"], exit_time=r["exit_time"],
        exit_reason=r["exit_reason"], realized_pnl=r["realized_pnl"],
        high_water_mark=r["high_water_mark"],
        broker=r["broker"] or "ibkr", account=r["account"] or "",
        legs=tuple(json.loads(r["legs_json"])),
        meta=json.loads(r["meta_json"] or "{}"),
    )


def _row_to_order(r: sqlite3.Row) -> Order:
    return Order(
        id=r["id"], position_id=r["position_id"], broker=r["broker"],
        broker_order_id=r["broker_order_id"], side=r["side"],
        limit_price=r["limit_price"], status=r["status"],
        submitted_at=r["submitted_at"], filled_at=r["filled_at"],
        fill_price=r["fill_price"], commission=r["commission"] or 0.0,
        kind=r["kind"] or "entry", idempotency_key=r["idempotency_key"],
    )


# ── Module-level helpers ───────────────────────────────────────────────────

_JOURNAL: Optional[Journal] = None
_JOURNAL_LOCK = threading.RLock()


def get_journal(db_path: Optional[str] = None) -> Journal:
    """Process-level singleton. Safe to call from request handlers."""
    global _JOURNAL
    with _JOURNAL_LOCK:
        if _JOURNAL is None:
            from core.settings import SETTINGS
            _JOURNAL = Journal(db_path or SETTINGS.journal_db_path)
        return _JOURNAL


def reset_journal_for_tests(db_path: str) -> Journal:  # pragma: no cover - test aid
    """Swap the global journal. Only call this from tests."""
    global _JOURNAL
    with _JOURNAL_LOCK:
        if _JOURNAL is not None:
            _JOURNAL.close()
        _JOURNAL = Journal(db_path)
        return _JOURNAL


__all__ = [
    "Journal",
    "Position",
    "Order",
    "Fill",
    "get_journal",
    "reset_journal_for_tests",
]
