import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { Card, Btn } from './primitives';

/**
 * Optimiser UI (use_request §3②).
 *
 * Schema-driven param_x / param_y selectors populated from the active
 * strategy's schema. Calls /api/optimize with comma-separated value
 * lists. Renders a simple PnL heatmap.
 */
export function OptimiserCard({ baseConfig }) {
  const stratId = baseConfig?.strategy_id || 'consecutive_days';
  const [schema, setSchema] = useState({});
  const [paramX, setParamX] = useState('entry_red_days');
  const [paramY, setParamY] = useState('target_dte');
  const [xVals, setXVals] = useState('1,2,3,4');
  const [yVals, setYVals] = useState('7,14,21,30');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.strategySchema(stratId)
      .then(res => setSchema(res.schema || {}))
      .catch(() => setSchema({}));
  }, [stratId]);

  const allParams = useMemo(() => {
    const fromSchema = Object.keys(schema);
    const common = ['target_dte', 'stop_loss_pct', 'take_profit_pct',
                    'trailing_stop_pct', 'strike_width', 'risk_percent'];
    return Array.from(new Set([...fromSchema, ...common]));
  }, [schema]);

  async function run() {
    setBusy(true); setErr(''); setResult(null);
    try {
      const parse = s => s.split(',').map(v => Number(v.trim())).filter(v => !Number.isNaN(v));
      const payload = {
        base_config: baseConfig || {},
        param_x: paramX, param_y: paramY,
        x_values: parse(xVals), y_values: parse(yVals),
      };
      const res = await api.runOptimize(payload);
      if (res.error) setErr(res.error);
      else setResult(res);
    } catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  }

  const cells = result?.results || [];
  const pnls = cells.map(c => c.pnl);
  const minP = Math.min(0, ...pnls), maxP = Math.max(0, ...pnls);
  const colorFor = (pnl) => {
    if (pnl > 0) {
      const t = maxP > 0 ? pnl / maxP : 0;
      return `rgba(34,197,94,${0.15 + 0.55 * t})`;
    }
    const t = minP < 0 ? pnl / minP : 0;
    return `rgba(239,68,68,${0.15 + 0.55 * t})`;
  };

  return (
    <Card title="Optimiser" icon="sliders" subtitle={`${stratId}`}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
        <label style={{ fontSize: 11 }}>
          <span className="muted">param X</span>
          <select value={paramX} onChange={e => setParamX(e.target.value)}
                  style={{ width: '100%', background: 'var(--bg-2)', color: 'var(--text)', border: '1px solid var(--border)', padding: '4px 6px', borderRadius: 4 }}>
            {allParams.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 11 }}>
          <span className="muted">param Y</span>
          <select value={paramY} onChange={e => setParamY(e.target.value)}
                  style={{ width: '100%', background: 'var(--bg-2)', color: 'var(--text)', border: '1px solid var(--border)', padding: '4px 6px', borderRadius: 4 }}>
            {allParams.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 11 }}>
          <span className="muted">X values (csv)</span>
          <input value={xVals} onChange={e => setXVals(e.target.value)}
                 style={{ width: '100%', background: 'var(--bg-2)', color: 'var(--text)', border: '1px solid var(--border)', padding: '4px 6px', borderRadius: 4 }} />
        </label>
        <label style={{ fontSize: 11 }}>
          <span className="muted">Y values (csv)</span>
          <input value={yVals} onChange={e => setYVals(e.target.value)}
                 style={{ width: '100%', background: 'var(--bg-2)', color: 'var(--text)', border: '1px solid var(--border)', padding: '4px 6px', borderRadius: 4 }} />
        </label>
      </div>
      <Btn variant="primary" onClick={run} disabled={busy}>
        {busy ? 'Running…' : 'Run Sweep'}
      </Btn>
      {err && <div style={{ color: 'var(--neg, #ef4444)', fontSize: 12, marginTop: 8 }}>{err}</div>}
      {cells.length > 0 && (
        <div style={{ marginTop: 12, overflow: 'auto' }}>
          <HeatmapTable cells={cells} paramX={paramX} paramY={paramY} colorFor={colorFor} />
        </div>
      )}
    </Card>
  );
}

function HeatmapTable({ cells, paramX, paramY, colorFor }) {
  const xs = Array.from(new Set(cells.map(c => c.x))).sort((a, b) => a - b);
  const ys = Array.from(new Set(cells.map(c => c.y))).sort((a, b) => a - b);
  const idx = new Map(cells.map(c => [`${c.x}|${c.y}`, c]));
  return (
    <table style={{ borderCollapse: 'collapse', fontSize: 11 }}>
      <thead>
        <tr>
          <th style={{ padding: 4 }}>{paramY} \ {paramX}</th>
          {xs.map(x => <th key={x} style={{ padding: 4 }}>{x}</th>)}
        </tr>
      </thead>
      <tbody>
        {ys.map(y => (
          <tr key={y}>
            <th style={{ padding: 4, textAlign: 'right' }}>{y}</th>
            {xs.map(x => {
              const c = idx.get(`${x}|${y}`);
              if (!c) return <td key={x} />;
              return (
                <td key={x} title={`${c.trades} trades · ${c.win_rate}% wr`}
                    style={{
                      padding: '6px 8px', textAlign: 'right', minWidth: 60,
                      background: colorFor(c.pnl), color: 'var(--text)',
                      border: '1px solid var(--border)',
                    }}>
                  {c.pnl >= 0 ? '+' : ''}{c.pnl.toFixed(0)}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
