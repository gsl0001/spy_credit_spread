import { useState, useRef, useMemo } from 'react';

export function EquityChart({ data: rawData, height = 260 }) {
  const data = useMemo(() => (rawData || []).filter(v => typeof v === 'number' && !isNaN(v)), [rawData]);
  const W = 1000, H = 100;
  const { mn, range } = useMemo(() => {
    if (data.length < 2) return { mn: 0, range: 1 };
    let mn = data[0], mx = data[0];
    for (let i = 1; i < data.length; i++) {
      if (data[i] < mn) mn = data[i];
      if (data[i] > mx) mx = data[i];
    }
    return { mn, range: mx - mn || 1 };
  }, [data]);

  if (!data || data.length < 2) return <div style={{ height, display:'grid', placeItems:'center', color:'var(--text-3)' }}>Insufficient data</div>;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * W},${H - ((v - mn) / range) * H}`).join(' ');
  const first = data[0], last = data[data.length - 1];
  const up = last >= first;
  const color = up ? 'var(--pos)' : 'var(--neg)';
  return (
    <div style={{ position: 'relative', height, width: '100%' }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: '100%', overflow: 'visible' }}>
        <defs>
          <linearGradient id="eqgrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.25" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={`0,${H} ${pts} ${W},${H}`} fill="url(#eqgrad)" />
        <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

export function CandlestickChart({ series, trades = [], height = '100%' }) {
  const [hover, setHover] = useState(null);
  const containerRef = useRef(null);

  const scaling = useMemo(() => {
    if (!series || series.length < 2) return { W: 1000, H: 100, chartH: 75, ddH: 20, yMin: 0, yMax: 0, yRange: 1 };
    
    let high = -Infinity, low = Infinity;
    let hasData = false;
    for (let i = 0; i < series.length; i++) {
      const h = Number(series[i].high), l = Number(series[i].low);
      if (!isNaN(h)) { high = Math.max(high, h); hasData = true; }
      if (!isNaN(l)) { low = Math.min(low, l); hasData = true; }
    }
    
    if (!hasData) { high = 100; low = 0; }
    const range = high - low || 1;
    const pad = range * 0.1;
    const yMin = low - pad, yMax = high + pad;
    const yRange = yMax - yMin || 1;
    
    return { mn: low, mx: high, range, yMin, yMax, yRange, W: 1000, H: 100, chartH: 75, ddH: 20 };
  }, [series]);

  const drawdowns = useMemo(() => {
    if (!series || series.length < 1) return [];
    let peak = -Infinity;
    return series.map(d => {
      const c = Number(d.close);
      if (!isNaN(c) && c > peak) peak = c;
      return (peak > 0 && !isNaN(c)) ? ((c - peak) / peak) * 100 : 0;
    });
  }, [series]);

  if (!series || series.length < 2) {
    return <div style={{ height: 400, display: 'grid', placeItems: 'center', color: 'var(--text-3)' }}>No price history</div>;
  }

  const { yMin, yMax, yRange, W, H, chartH, ddH } = scaling;
  const x = i => (i / (series.length - 1)) * W;
  const y = v => chartH - (((Number(v) || 0) - yMin) / yRange) * chartH;
  const candleW = (W / series.length) * 0.7;

  const handleMouseMove = (e) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const idx = Math.max(0, Math.min(series.length - 1, Math.round((px / W) * (series.length - 1))));
    setHover(idx);
  };

  const current = hover !== null ? series[hover] : series[series.length - 1];
  const currentDd = hover !== null ? (drawdowns[hover] ?? 0) : (drawdowns[drawdowns.length - 1] ?? 0);

  if (!current) return null;

  return (
    <div 
      ref={containerRef}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => setHover(null)}
      style={{ position: 'relative', height, width: '100%', background: 'var(--bg-0)', borderRadius: 8, cursor: 'crosshair', userSelect: 'none' }}
    >
      {/* Legend / Tooltip */}
      <div style={{ position: 'absolute', top: 12, left: 16, zIndex: 10, pointerEvents: 'none', display: 'flex', gap: 12, fontSize: 11, fontFamily: 'var(--font-mono)' }}>
        <div style={{ color: 'var(--text-3)' }}>{current.time || '—'}</div>
        <div>O: <span style={{ color: 'var(--text)' }}>{(Number(current.open) || 0).toFixed(2)}</span></div>
        <div>H: <span style={{ color: 'var(--pos)' }}>{(Number(current.high) || 0).toFixed(2)}</span></div>
        <div>L: <span style={{ color: 'var(--neg)' }}>{(Number(current.low) || 0).toFixed(2)}</span></div>
        <div>C: <span style={{ color: 'var(--text)' }}>{(Number(current.close) || 0).toFixed(2)}</span></div>
        <div style={{ color: 'var(--neg)' }}>DD: {(Number(currentDd) || 0).toFixed(2)}%</div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: '100%', overflow: 'visible' }}>
        {/* Horizontal Grids */}
        {[0, 0.25, 0.5, 0.75].map(f => (
          <line key={f} x1="0" x2={W} y1={chartH * f} y2={chartH * f} stroke="var(--border-soft)" strokeWidth="0.2" />
        ))}

        {/* Drawdown Section Divider */}
        <line x1="0" x2={W} y1={chartH + 2} y2={chartH + 2} stroke="var(--border)" strokeWidth="0.5" />
        <text x="4" y={chartH + 8} fill="var(--text-4)" fontSize="3">DRAWDOWN</text>

        {/* Drawdown Fill */}
        <polyline
          points={drawdowns.map((dd, i) => `${x(i)},${H - (Math.abs(dd) / 10) * ddH}`).join(' ')}
          fill="none" stroke="var(--neg)" strokeWidth="0.5" opacity="0.6"
        />

        {/* Candles */}
        {series.map((d, i) => {
          const cx = x(i), cOpen = y(d.open), cClose = y(d.close), cHigh = y(d.high), cLow = y(d.low);
          const isUp = d.close >= d.open;
          const color = isUp ? 'var(--pos)' : 'var(--neg)';
          const active = hover === i;
          return (
            <g key={i} opacity={hover !== null && !active ? 0.3 : 1}>
              <line x1={cx} x2={cx} y1={cHigh} y2={cLow} stroke={color} strokeWidth="0.4" />
              <rect x={cx - candleW/2} y={Math.min(cOpen, cClose)} width={candleW} height={Math.max(0.3, Math.abs(cOpen - cClose))} fill={color} />
            </g>
          );
        })}

        {/* Trades */}
        {(() => {
          const timeToIdx = new Map(series.map((s, i) => [s.time, i]));
          return trades.map((t, i) => {
            const idx = timeToIdx.get(t.entry_date);
            if (idx === undefined || t.entry_price == null) return null;
            
            const exitIdx = t.exit_date ? (timeToIdx.get(t.exit_date) ?? -1) : -1;
            const tx = x(idx), ty = y(t.entry_price);
            const win = t.pnl > 0;

            if (isNaN(tx) || isNaN(ty)) return null;
            
            return (
              <g key={i}>
                <circle cx={tx} cy={ty} r="2.5" fill="var(--pos)" stroke="#fff" strokeWidth="0.3" />
                {exitIdx !== -1 && t.exit_price != null && !isNaN(y(t.exit_price)) && (
                  <>
                    <line x1={tx} y1={ty} x2={x(exitIdx)} y2={y(t.exit_price)} stroke={win ? 'var(--pos)' : 'var(--neg)'} strokeWidth="0.4" strokeDasharray="1,1" opacity="0.4" />
                    <circle cx={x(exitIdx)} cy={y(t.exit_price)} r="2.5" fill="var(--neg)" stroke="#fff" strokeWidth="0.3" />
                  </>
                )}
              </g>
            );
          });
        })()}

        {/* Crosshair */}
        {hover !== null && (
          <g pointerEvents="none">
            <line x1={x(hover)} x2={x(hover)} y1="0" y2={H} stroke="var(--accent)" strokeWidth="0.5" strokeDasharray="2,2" />
            <circle cx={x(hover)} cy={y(current.close)} r="3" fill="var(--accent)" />
          </g>
        )}
      </svg>

      {/* Price Labels */}
      <div style={{ position: 'absolute', right: 8, top: 4, fontSize: 9, color: 'var(--text-3)', fontFamily: 'var(--font-mono)' }}>${(Number(yMax) || 0).toFixed(2)}</div>
      <div style={{ position: 'absolute', right: 8, top: chartH + '%', transform: 'translateY(-100%)', fontSize: 9, color: 'var(--text-3)', fontFamily: 'var(--font-mono)' }}>${(Number(yMin) || 0).toFixed(2)}</div>
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
