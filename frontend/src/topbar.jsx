import { useState, useCallback, useEffect } from 'react';
import { Ico } from './icons.jsx';
import { Pill, Sparkline } from './primitives.jsx';
import { api } from './api.js';

const LS_IBKR_AUTO = 'spy_ibkr_auto_reconnect';
const LS_MOOMOO_AUTO = 'spy_moomoo_auto_reconnect';

function BrokerPill({ label, icon, status, reconnecting, attempt, autoOn, onToggleAuto }) {
  const statusLabel = reconnecting
    ? `RECON #${attempt}`
    : status === 'live' ? 'ON'
    : status === 'warn' ? 'STALE'
    : 'OFF';

  const kind = reconnecting ? 'warn' : status;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
      <Pill kind={kind}>
        <Ico name={icon} size={11} /> {label} {statusLabel}
      </Pill>
      <button
        onClick={onToggleAuto}
        title={`Auto-reconnect: ${autoOn ? 'ON — click to disable' : 'OFF — click to enable'}`}
        style={{
          background: 'none',
          border: `1px solid ${autoOn ? 'var(--pos)' : 'var(--border-soft)'}`,
          borderRadius: 4,
          padding: '3px 4px',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: autoOn ? 'var(--pos)' : 'var(--text-3)',
          transition: 'all .2s',
          opacity: autoOn ? 1 : 0.5,
        }}
      >
        <Ico name="refresh" size={12} />
      </button>
    </div>
  );
}

export function Topbar({ view, mkt, spy, conn, onPanic, leader, onBell, alertCount }) {
  const titles = {
    live:     ['Live Trading',      'IBKR · SPY Bull Call Spreads'],
    paper:    ['Paper Trading',     'Alpaca · Signal Surrogate'],
    moomoo:   ['Moomoo Trading',    'moomoo OpenD · legged spreads'],
    backtest: ['Backtest',          'Strategy Research & Parameter Sweep'],
    journal:  ['Trade Journal',     'Positions, Orders, P&L History'],
    risk:     ['Risk & Guardrails', 'Pre-trade gates, sizing, event blackout'],
    scanner:  ['Scanner',           'Signal Feed with Persistence'],
  };
  const [title, sub] = titles[view] || ['', ''];

  const [ibkrAuto, setIbkrAuto] = useState(() => localStorage.getItem(LS_IBKR_AUTO) !== 'false');
  const [moomooAuto, setMoomooAuto] = useState(() => localStorage.getItem(LS_MOOMOO_AUTO) !== 'false');

  // On mount: push the user's saved preference to the backend so the
  // server-side gates match the UI immediately (no stale auto-reconnect storms
  // until the user clicks the toggle).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const remote = await api.connectionAutoGet();
        if (cancelled || !remote) return;
        const localIbkr = localStorage.getItem(LS_IBKR_AUTO);
        const localMoo  = localStorage.getItem(LS_MOOMOO_AUTO);
        if (localIbkr !== null && Boolean(remote.ibkr) !== (localIbkr !== 'false')) {
          api.connectionAutoSet('ibkr', localIbkr !== 'false').catch(() => {});
        } else if (localIbkr === null) {
          setIbkrAuto(Boolean(remote.ibkr));
        }
        if (localMoo !== null && Boolean(remote.moomoo) !== (localMoo !== 'false')) {
          api.connectionAutoSet('moomoo', localMoo !== 'false').catch(() => {});
        } else if (localMoo === null) {
          setMoomooAuto(Boolean(remote.moomoo));
        }
      } catch { /* backend unreachable — UI keeps localStorage state */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const toggleIbkrAuto = useCallback(() => {
    setIbkrAuto(prev => {
      const next = !prev;
      localStorage.setItem(LS_IBKR_AUTO, String(next));
      api.connectionAutoSet('ibkr', next).catch(() => {});
      return next;
    });
  }, []);

  const toggleMoomooAuto = useCallback(() => {
    setMoomooAuto(prev => {
      const next = !prev;
      localStorage.setItem(LS_MOOMOO_AUTO, String(next));
      api.connectionAutoSet('moomoo', next).catch(() => {});
      return next;
    });
  }, []);

  const moo = conn.moomoo || { status: 'off', reconnecting: false, attempt: 0 };

  return (
    <header className="topbar">
      <div className="topbar__ctx">
        <div>
          <div className="topbar__title">{title}</div>
          <div className="topbar__sub">{sub}</div>
        </div>
      </div>

      <div className={`mkt-status ${mkt.open ? 'open' : 'closed'}`}>
        <span className="dot" />MKT {mkt.open ? 'OPEN' : 'CLOSED'} · {mkt.next}
      </div>

      <div className="topbar__quote">
        <span className="sym">SPY</span>
        {spy.source === 'ibkr' && <span style={{ fontSize: 9, background: 'var(--accent)', color: 'var(--bg-0)', padding: '1px 4px', borderRadius: 3, fontWeight: 800, marginRight: 4 }}>IBKR</span>}
        <span className="px">${spy.current.toFixed(2)}</span>
        <span className="chg" style={{ color: spy.change >= 0 ? 'var(--pos)' : 'var(--neg)' }}>
          {spy.change >= 0 ? '+' : ''}{spy.change.toFixed(2)} ({spy.change >= 0 ? '+' : ''}{spy.change_pct.toFixed(2)}%)
        </span>
        <Sparkline data={spy.series} color={spy.change >= 0 ? 'var(--pos)' : 'var(--neg)'} width={80} height={22} />
      </div>

      <div className="topbar__spacer" />

      <Pill kind={leader.is_leader ? 'live' : 'warn'}>
        <Ico name="lock" size={11} /> {leader.is_leader ? 'LEADER' : 'FOLLOWER'}
      </Pill>

      <BrokerPill
        label="IBKR"
        icon="server"
        status={conn.ibkr}
        reconnecting={false}
        attempt={0}
        autoOn={ibkrAuto}
        onToggleAuto={toggleIbkrAuto}
      />

      <BrokerPill
        label="MOOMOO"
        icon="zap"
        status={moo.status}
        reconnecting={moo.reconnecting}
        attempt={moo.attempt}
        autoOn={moomooAuto}
        onToggleAuto={toggleMoomooAuto}
      />

      <button className="btn ghost icon" onClick={onBell} style={{ position: 'relative' }}>
        <Ico name="bell" size={16} />
        {alertCount > 0 && (
          <span style={{
            position: 'absolute', top: 3, right: 3, minWidth: 14, height: 14,
            padding: '0 3px', borderRadius: 8, background: 'var(--neg)', color: 'white',
            fontSize: 9, fontWeight: 700, display: 'grid', placeItems: 'center',
          }}>{alertCount}</span>
        )}
      </button>

      <button className="panic-btn" onClick={onPanic}>
        <Ico name="power" size={14} />FLATTEN ALL
      </button>
    </header>
  );
}
