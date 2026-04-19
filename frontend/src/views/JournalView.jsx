import { useState } from 'react';
import { useData } from '../useBackendData.jsx';
import { fmtUsd, fmtPct, fmtTimeAgo, Card, Kpi, Badge, Btn } from '../primitives.jsx';
import { EquityChart, PnlBars } from '../chart.jsx';

export function JournalView() {
  const m = useData();
  const [tab, setTab] = useState('orders');
  const totalPnl = m.dailyPnl.reduce((s, d) => s + d.pnl, 0);
  const wins = m.closed.filter(c => c.pnl > 0).length;
  const losses = m.closed.filter(c => c.pnl < 0).length;
  const winRate = (wins + losses) > 0 ? Math.round(wins / (wins + losses) * 100) : 0;
  const avgWin = wins > 0 ? m.closed.filter(c => c.pnl > 0).reduce((s, c) => s + c.pnl, 0) / wins : 0;
  const avgLoss = losses > 0 ? Math.abs(m.closed.filter(c => c.pnl < 0).reduce((s, c) => s + c.pnl, 0) / losses) : 0;
  const winLossR = avgLoss > 0 ? (avgWin / avgLoss).toFixed(2) : '—';

  return (
    <div className="page">
      <div className="grid g-4" style={{ marginBottom: 14 }}>
        <Card><Kpi label="30d Realized" value={fmtUsd(totalPnl, true)} color={totalPnl >= 0 ? 'var(--pos)' : 'var(--neg)'} big /></Card>
        <Card><Kpi label="Win Rate" value={`${winRate}%`} delta={{ text: `${wins}W / ${losses}L` }} big /></Card>
        <Card><Kpi label="Avg Win / Loss" value={winLossR} suffix={winLossR !== '—' ? 'R' : ''} delta={{ text: `+$${Math.round(avgWin)} / −$${Math.round(avgLoss)} avg` }} big /></Card>
        <Card><Kpi label="Closed trades" value={m.closed.length} delta={{ text: `${m.positions.length} still open` }} big /></Card>
      </div>

      <div className="grid g-23" style={{ marginBottom: 14 }}>
        <Card title="Equity curve" subtitle="YTD" icon="trending" actions={
          <><Btn variant="ghost" size="sm">1M</Btn><Btn size="sm">YTD</Btn><Btn variant="ghost" size="sm">All</Btn></>
        }>
          <EquityChart data={m.backtest.equity.slice(-60)} height={240} />
        </Card>
        <Card title="Daily P&L" icon="activity" subtitle="last 12 sessions">
          <PnlBars data={m.dailyPnl} height={240} />
        </Card>
      </div>

      <Card flush>
        <div className="tabs" style={{ borderTop: 'none' }}>
          {['orders', 'closed positions', 'fills', 'events'].map(t => (
            <button key={t} className="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}</button>
          ))}
          <div style={{ flex: 1 }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: 6 }}>
            <Btn variant="ghost" size="sm" icon="search">Filter</Btn>
            <Btn variant="ghost" size="sm" icon="download">Export</Btn>
          </div>
        </div>

        {tab === 'orders' && (
          <table className="tbl">
            <thead><tr><th>Order</th><th>Kind</th><th>Side</th><th className="num">Qty</th><th className="num">Limit</th><th className="num">Fill</th><th>Status</th><th>Idempotency</th><th>Time</th></tr></thead>
            <tbody>
              {m.orders.length === 0 && (
                <tr><td colSpan="9" style={{ textAlign: 'center', padding: 24, color: 'var(--text-3)', fontSize: 12 }}>No orders</td></tr>
              )}
              {m.orders.map(o => (
                <tr key={o.id}>
                  <td><span className="mono">{o.id}</span><div className="muted" style={{ fontSize: 10.5 }}>→ {o.pos || '—'}</div></td>
                  <td><Badge variant={o.kind === 'entry' ? 'info' : 'neutral'}>{o.kind}</Badge></td>
                  <td><Badge variant={o.side === 'BUY' ? 'pos' : 'neg'} dot>{o.side}</Badge></td>
                  <td className="num">{o.qty}</td>
                  <td className="num">${o.lmt.toFixed(2)}</td>
                  <td className="num">{o.fill ? `$${o.fill.toFixed(2)}` : '—'}</td>
                  <td><Badge variant={o.status === 'filled' ? 'pos' : o.status === 'pending' ? 'warn' : 'neutral'}>{o.status}</Badge></td>
                  <td><span className="mono" style={{ fontSize: 11, color: 'var(--text-3)' }}>{o.idem}</span></td>
                  <td className="muted mono" style={{ fontSize: 11 }}>{fmtTimeAgo(o.submitted)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === 'closed positions' && (
          <table className="tbl">
            <thead><tr><th>ID</th><th>Legs</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Exit</th><th className="num">P&L</th><th>Reason</th><th>Held</th><th>Closed</th></tr></thead>
            <tbody>
              {m.closed.length === 0 && (
                <tr><td colSpan="9" style={{ textAlign: 'center', padding: 24, color: 'var(--text-3)', fontSize: 12 }}>No closed positions</td></tr>
              )}
              {m.closed.map(c => (
                <tr key={c.id}>
                  <td><span className="mono">{c.id}</span></td>
                  <td><span className="mono">{c.legs}</span></td>
                  <td className="num">{c.contracts}</td>
                  <td className="num">${c.entry.toFixed(2)}</td>
                  <td className="num">${c.exit.toFixed(2)}</td>
                  <td className="num" style={{ color: c.pnl >= 0 ? 'var(--pos)' : 'var(--neg)', fontWeight: 600 }}>
                    {fmtUsd(c.pnl, true)} <span className="muted" style={{ fontSize: 11 }}>({fmtPct(c.pnl_pct)})</span>
                  </td>
                  <td><Badge variant={c.reason === 'take_profit' || c.reason === 'trailing' ? 'pos' : c.reason === 'stop_loss' || c.reason === 'expired' ? 'neg' : 'neutral'}>{c.reason}</Badge></td>
                  <td className="mono muted" style={{ fontSize: 11 }}>{c.held_days}d</td>
                  <td className="mono muted" style={{ fontSize: 11 }}>{fmtTimeAgo(c.closed)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === 'fills' && (
          <table className="tbl">
            <thead><tr><th>Fill</th><th>Order</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Commission</th><th>Exec ID</th><th>Time</th></tr></thead>
            <tbody>
              {m.orders.filter(o => o.status === 'filled').length === 0 && (
                <tr><td colSpan="7" style={{ textAlign: 'center', padding: 24, color: 'var(--text-3)', fontSize: 12 }}>No fills</td></tr>
              )}
              {m.orders.filter(o => o.status === 'filled').map((o) => (
                <tr key={o.id}>
                  <td className="mono">{o.fill_id || '—'}</td>
                  <td className="mono">{o.id}</td>
                  <td className="num">{o.qty}</td>
                  <td className="num">${o.fill.toFixed(2)}</td>
                  <td className="num">${o.commission.toFixed(2)}</td>
                  <td className="mono muted" style={{ fontSize: 11 }}>{o.exec_id || '—'}</td>
                  <td className="muted mono" style={{ fontSize: 11 }}>{fmtTimeAgo(o.submitted)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === 'events' && (
          <div>
            {(() => {
              const events = [];
              if (m.monitor.leader_info.acquired_at) {
                events.push({ t: 'monitor_started', m: 'Scheduler registered monitor_tick + fill_reconcile + daily_digest', level: 'info', time: m.monitor.leader_info.acquired_at });
                events.push({ t: 'leader_acquired', m: 'fcntl advisory lock acquired on data/monitor.lock', level: 'info', time: m.monitor.leader_info.acquired_at });
              }
              if (m.orders[0]) {
                events.push({ t: 'order_submitted', m: `${m.orders[0].pos || m.orders[0].id} submitted`, level: 'info', time: m.orders[0].submitted });
                if (m.orders[0].status === 'filled') {
                  events.push({ t: 'order_filled', m: `${m.orders[0].id} filled @ ${m.orders[0].fill?.toFixed(2) ?? '—'}`, level: 'info', time: m.orders[0].submitted });
                }
              }
              if (m.closed[0]) events.push({ t: 'exit_signal', m: `${m.closed[0].id} · ${m.closed[0].reason}`, level: 'info', time: m.closed[0].closed });
              if (m.digest.last_sent) events.push({ t: 'digest_sent', m: `webhook → HTTP ${m.digest.last_status ?? '—'}`, level: 'info', time: m.digest.last_sent });
              if (events.length === 0) return <div className="muted" style={{ padding: 24, textAlign: 'center', fontSize: 12 }}>No events yet</div>;
              return events.map((e, i) => (
                <div key={i} className="link-row">
                  <Badge variant={e.level === 'warn' ? 'warn' : 'info'} dot>{e.t}</Badge>
                  <span>{e.m}</span>
                  <span className="muted mono" style={{ marginLeft: 'auto', fontSize: 11 }}>{fmtTimeAgo(e.time)}</span>
                </div>
              ));
            })()}
          </div>
        )}
      </Card>
    </div>
  );
}
