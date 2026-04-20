import { useData } from '../useBackendData.jsx';
import { fmtUsd, Card, Kpi, Badge, Btn } from '../primitives.jsx';

export function PaperView() {
  const m = useData();
  const paper = m.paper || { equity: 0, day_pnl: 0, positions: [], auto_execute: false, api_key_set: false };
  const pos = paper.positions || [];

  return (
    <div className="page">
      <div className="grid g-4" style={{ marginBottom: 14 }}>
        <Card><Kpi label="Paper Equity" value={fmtUsd(paper.equity)} delta={{ text: 'Alpaca paper', color: 'var(--text-3)' }} big /></Card>
        <Card><Kpi label="Day P&L" value={fmtUsd(paper.day_pnl, true)} color={paper.day_pnl >= 0 ? 'var(--pos)' : 'var(--neg)'} big /></Card>
        <Card><Kpi label="Open" value={pos.length} delta={{ text: pos.length ? 'SPY surrogate' : '—' }} big /></Card>
        <Card><Kpi label="Auto-execute" value={paper.auto_execute ? 'ON' : 'OFF'} delta={{ text: 'manual signal confirm', color: 'var(--warn)' }} big /></Card>
      </div>

      <div className="grid g-23">
        <Card title="Paper positions (equity surrogate)" icon="dashboard" subtitle="Alpaca doesn't support spreads — we trade 100-sh SPY as a proxy" flush>
          <table className="tbl">
            <thead>
              <tr>
                <th>Symbol</th><th className="num">Qty</th><th>Side</th>
                <th className="num">Avg</th><th className="num">Mark</th>
                <th className="num">Mkt Val</th><th className="num">Unrealized</th>
              </tr>
            </thead>
            <tbody>
              {pos.length === 0 && (
                <tr><td colSpan="7" style={{ textAlign: 'center', padding: 24, color: 'var(--text-3)' }}>No paper positions</td></tr>
              )}
              {pos.map((p, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{p.symbol}</td>
                  <td className="num">{p.qty}</td>
                  <td><Badge variant={p.side === 'LONG' ? 'pos' : 'neg'} dot>{p.side}</Badge></td>
                  <td className="num">${p.avg.toFixed(2)}</td>
                  <td className="num">${p.mark.toFixed(2)}</td>
                  <td className="num">${p.mkt_val.toFixed(2)}</td>
                  <td className="num" style={{ color: p.unrealized >= 0 ? 'var(--pos)' : 'var(--neg)' }}>
                    {fmtUsd(p.unrealized, true)} ({p.unrealized_pct >= 0 ? '+' : ''}{p.unrealized_pct?.toFixed(2)}%)
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Connection" icon="wifi">
          <div className="field" style={{ marginBottom: 10 }}>
            <label>API Key</label>
            <input className="inp" type="password" placeholder="PK…" defaultValue="" />
          </div>
          <div className="field" style={{ marginBottom: 12 }}>
            <label>Secret</label>
            <input className="inp" type="password" placeholder="secret" defaultValue="" />
          </div>
          <Btn variant="primary" icon="wifi" style={{ width: '100%', justifyContent: 'center' }}>Reconnect</Btn>
          <hr className="sep" />
          <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
            Scanner routes paper signals to equity orders; spread construction only in Live.
          </div>
        </Card>
      </div>
    </div>
  );
}
