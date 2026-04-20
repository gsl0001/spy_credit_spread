import { Ico } from './icons.jsx';
import { Pill, Sparkline } from './primitives.jsx';

export function Topbar({ view, mkt, spy, conn, onPanic, leader, onBell, alertCount }) {
  const titles = {
    live:     ['Live Trading',      'IBKR · SPY Bull Call Spreads'],
    paper:    ['Paper Trading',     'Alpaca · Signal Surrogate'],
    backtest: ['Backtest',          'Strategy Research & Parameter Sweep'],
    journal:  ['Trade Journal',     'Positions, Orders, P&L History'],
    risk:     ['Risk & Guardrails', 'Pre-trade gates, sizing, event blackout'],
    scanner:  ['Scanner',           'Signal Feed with Persistence'],
  };
  const [title, sub] = titles[view] || ['', ''];

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

      <Pill kind={conn.ibkr}>
        <Ico name="server" size={11} /> IBKR {conn.ibkr === 'live' ? 'LIVE' : conn.ibkr === 'warn' ? 'RECON' : 'OFF'}
      </Pill>

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
