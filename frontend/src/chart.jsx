export function EquityChart({ data, height = 260 }) {
  if (!data || data.length < 2) return null;
  const W = 100, H = 100;
  const mn = Math.min(...data), mx = Math.max(...data), range = mx - mn || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * W},${H - ((v - mn) / range) * H}`).join(' ');
  const first = data[0], last = data[data.length - 1];
  const up = last >= first;
  const color = up ? 'var(--pos)' : 'var(--neg)';
  return (
    <div style={{ position: 'relative', height, width: '100%' }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: '100%' }}>
        <defs>
          <linearGradient id="eqgrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.25" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map(f => <line key={f} x1="0" x2={W} y1={H * f} y2={H * f} stroke="var(--border-soft)" strokeWidth="0.2" />)}
        <polygon points={`0,${H} ${pts} ${W},${H}`} fill="url(#eqgrad)" />
        <polyline points={pts} fill="none" stroke={color} strokeWidth="0.5" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

export function PnlBars({ data, height = 120 }) {
  if (!data || !data.length) return null;
  const values = data.map(d => d.pnl);
  const max = Math.max(...values.map(Math.abs)) || 1;
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height, padding: '6px 4px 22px', position: 'relative' }}>
      <div style={{ position: 'absolute', left: 0, right: 0, top: '50%', borderTop: '1px dashed var(--border-soft)' }} />
      {data.map((d, i) => {
        const h = Math.abs(d.pnl) / max * (height / 2 - 14);
        const up = d.pnl >= 0;
        return (
          <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', height: '100%' }}>
            <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', width: '100%' }}>
              {up && <div style={{ width: '100%', height: h, background: 'var(--pos)', borderRadius: '2px 2px 0 0', opacity: 0.85 }} />}
            </div>
            <div style={{ flex: 1, display: 'flex', alignItems: 'flex-start', width: '100%' }}>
              {!up && <div style={{ width: '100%', height: h, background: 'var(--neg)', borderRadius: '0 0 2px 2px', opacity: 0.85 }} />}
            </div>
            <div style={{ fontSize: 9, color: 'var(--text-4)', fontFamily: 'var(--font-mono)', position: 'absolute', bottom: 4, transform: 'translateX(-50%)', left: `${(i + 0.5) / data.length * 100}%` }}>{d.date}</div>
          </div>
        );
      })}
    </div>
  );
}

export function PriceChart({ series, height = 320 }) {
  if (!series || series.length < 2) {
    return (
      <div style={{ height, display: 'grid', placeItems: 'center', color: 'var(--text-3)', fontSize: 12 }}>
        No intraday data
      </div>
    );
  }
  const mn = Math.min(...series), mx = Math.max(...series), range = mx - mn || 1;
  const pad = range * 0.1;
  const yMin = mn - pad, yMax = mx + pad, yRange = yMax - yMin;
  const W = 100, H = 100;
  const y = v => H - ((v - yMin) / yRange) * H;
  const pts = series.map((v, i) => `${(i / (series.length - 1)) * W},${y(v)}`).join(' ');
  const up = series[series.length - 1] >= series[0];
  const color = up ? 'var(--pos)' : 'var(--neg)';

  return (
    <div style={{ position: 'relative', height, width: '100%', padding: '8px 0' }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: '100%' }}>
        <defs>
          <linearGradient id="pxgrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.22" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.2, 0.4, 0.6, 0.8].map(f => <line key={f} x1="0" x2={W} y1={H * f} y2={H * f} stroke="var(--border-soft)" strokeWidth="0.15" />)}
        <polygon points={`0,${H} ${pts} ${W},${H}`} fill="url(#pxgrad)" />
        <polyline points={pts} fill="none" stroke={color} strokeWidth="0.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div style={{ position: 'absolute', right: 6, top: 6, fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>${yMax.toFixed(2)}</div>
      <div style={{ position: 'absolute', right: 6, bottom: 6, fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>${yMin.toFixed(2)}</div>
    </div>
  );
}
