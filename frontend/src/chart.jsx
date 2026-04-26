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

  if (!data || data.length < 2) return <div style={{ height, display: 'grid', placeItems: 'center', color: 'var(--text-3)' }}>Insufficient data</div>;
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

export function CandlestickChart({ series, trades = [], height = '100%', selectedTrade = null, onTradeSelect }) {
  const [hover, setHover] = useState(null);
  const [hoverTrade, setHoverTrade] = useState(null);
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

  // Pre-compute trade geometry + index map for interactivity
  const tradeGeom = useMemo(() => {
    if (!series || series.length < 2) return { items: [], byEntryIdx: new Map() };
    const { yMin: yMn, yRange: yRn, W, chartH } = scaling;
    const x = i => (i / (series.length - 1)) * W;
    const y = v => chartH - (((Number(v) || 0) - yMn) / yRn) * chartH;
    const timeToIdx = new Map(series.map((s, i) => [s.time, i]));

    const items = trades.map((t, i) => {
      const ei = timeToIdx.get(t.entry_date);
      if (ei === undefined || t.entry_price == null) return null;
      const xi = t.exit_date ? (timeToIdx.get(t.exit_date) ?? -1) : -1;
      const ex = ei !== undefined ? x(ei) : null;
      const ey = t.entry_price != null ? y(t.entry_price) : null;
      const xx = xi !== -1 ? x(xi) : null;
      const xy = xi !== -1 && t.exit_price != null ? y(t.exit_price) : null;
      if (ex == null || ey == null || isNaN(ex) || isNaN(ey)) return null;
      return {
        i, trade: t, entryIdx: ei, exitIdx: xi,
        ex, ey, xx, xy,
        win: (t.pnl || 0) > 0,
      };
    }).filter(Boolean);

    const byEntryIdx = new Map();
    items.forEach(it => {
      if (!byEntryIdx.has(it.entryIdx)) byEntryIdx.set(it.entryIdx, []);
      byEntryIdx.get(it.entryIdx).push(it);
    });

    return { items, byEntryIdx };
  }, [scaling, series, trades]);

  if (!series || series.length < 2) {
    return <div style={{ height, display: 'grid', placeItems: 'center', color: 'var(--text-3)', fontSize: 12 }}>No price history — run a simulation</div>;
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
  const hoveredEntryTrades = hover !== null ? (tradeGeom.byEntryIdx.get(hover) || []) : [];

  if (!current) return null;

  const activeTradeItem =
    (hoverTrade != null && tradeGeom.items.find(it => it.i === hoverTrade)) ||
    (selectedTrade != null && tradeGeom.items.find(it => it.i === selectedTrade)) ||
    null;

  return (
    <div
      ref={containerRef}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => { setHover(null); setHoverTrade(null); }}
      style={{ position: 'relative', height, width: '100%', background: 'var(--bg-0)', borderRadius: 4, cursor: 'crosshair', userSelect: 'none', overflow: 'hidden' }}
    >
      {/* OHLC legend */}
      <div style={{ position: 'absolute', top: 8, left: 12, zIndex: 10, pointerEvents: 'none', display: 'flex', gap: 10, fontSize: 11, fontFamily: 'var(--font-mono)', flexWrap: 'wrap', maxWidth: 'calc(100% - 24px)' }}>
        <span style={{ color: 'var(--text-3)' }}>{current.time || '—'}</span>
        <span>O <span style={{ color: 'var(--text)' }}>{(Number(current.open) || 0).toFixed(2)}</span></span>
        <span>H <span style={{ color: 'var(--pos)' }}>{(Number(current.high) || 0).toFixed(2)}</span></span>
        <span>L <span style={{ color: 'var(--neg)' }}>{(Number(current.low) || 0).toFixed(2)}</span></span>
        <span>C <span style={{ color: 'var(--text)' }}>{(Number(current.close) || 0).toFixed(2)}</span></span>
        <span style={{ color: 'var(--neg)' }}>DD {(Number(currentDd) || 0).toFixed(2)}%</span>
      </div>

      {/* Trade info bar (when hovering/selected) */}
      {activeTradeItem && (
        <div style={{
          position: 'absolute', top: 34, left: 12, zIndex: 11, pointerEvents: 'none',
          display: 'flex', gap: 10, fontSize: 11, fontFamily: 'var(--font-mono)',
          padding: '4px 10px', borderRadius: 4,
          background: activeTradeItem.win ? 'var(--pos-bg)' : 'var(--neg-bg)',
          color: activeTradeItem.win ? 'var(--pos)' : 'var(--neg)',
          border: `1px solid ${activeTradeItem.win ? 'var(--pos)' : 'var(--neg)'}`,
          fontWeight: 600,
        }}>
          <span>{activeTradeItem.trade.side || 'TRADE'}</span>
          <span>{activeTradeItem.trade.entry_date} → {activeTradeItem.trade.exit_date || '—'}</span>
          <span>{(activeTradeItem.trade.pnl || 0) >= 0 ? '+' : ''}${(activeTradeItem.trade.pnl || 0).toFixed(2)}</span>
          {activeTradeItem.trade.exit_reason && <span style={{ opacity: 0.7 }}>· {activeTradeItem.trade.exit_reason}</span>}
        </div>
      )}

      {/* Hover summary: count of trades opened on hovered candle */}
      {hover !== null && hoveredEntryTrades.length > 0 && !activeTradeItem && (
        <div style={{
          position: 'absolute', top: 34, left: 12, zIndex: 11, pointerEvents: 'none',
          fontSize: 11, fontFamily: 'var(--font-mono)',
          padding: '4px 10px', borderRadius: 4,
          background: 'var(--bg-2)', color: 'var(--text-2)', border: '1px solid var(--border)',
        }}>
          {hoveredEntryTrades.length} trade{hoveredEntryTrades.length > 1 ? 's' : ''} opened here
        </div>
      )}

      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: '100%', overflow: 'visible' }}>
        {/* Grid lines */}
        {[0, 0.25, 0.5, 0.75].map(f => (
          <line key={f} x1="0" x2={W} y1={chartH * f} y2={chartH * f} stroke="var(--border-soft)" strokeWidth="0.2" />
        ))}

        {/* Drawdown divider + label */}
        <line x1="0" x2={W} y1={chartH + 2} y2={chartH + 2} stroke="var(--border)" strokeWidth="0.5" />
        <text x="4" y={chartH + 8} fill="var(--text-4)" fontSize="3">DRAWDOWN</text>

        {/* Drawdown line */}
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
            <g key={i} opacity={hover !== null && !active ? 0.35 : 1}>
              <line x1={cx} x2={cx} y1={cHigh} y2={cLow} stroke={color} strokeWidth="0.4" />
              <rect x={cx - candleW / 2} y={Math.min(cOpen, cClose)} width={candleW} height={Math.max(0.3, Math.abs(cOpen - cClose))} fill={color} />
            </g>
          );
        })}

        {/* Trades — markers + connecting lines */}
        {tradeGeom.items.map(it => {
          const isActive = (hoverTrade === it.i) || (selectedTrade === it.i);
          const isDimmed = (hoverTrade != null && hoverTrade !== it.i) || (selectedTrade != null && selectedTrade !== it.i);
          const winColor = it.win ? 'var(--pos)' : 'var(--neg)';
          const entryR = isActive ? 2.2 : 1.4;
          const exitR  = isActive ? 2.2 : 1.4;
          const strokeW = isActive ? 0.5 : 0.3;
          const op = isDimmed ? 0.25 : 1;

          return (
            <g key={it.i} opacity={op} style={{ cursor: 'pointer' }}>
              {/* Connection line */}
              {it.xx != null && it.xy != null && (
                <line
                  x1={it.ex} y1={it.ey} x2={it.xx} y2={it.xy}
                  stroke={winColor} strokeWidth={isActive ? 0.8 : 0.5}
                  strokeDasharray={isActive ? '0' : '1.5,1'}
                  opacity={isActive ? 0.9 : 0.6}
                />
              )}
              {/* Entry marker (accent / blue-ish) */}
              <circle
                cx={it.ex} cy={it.ey} r={entryR}
                fill="var(--info)" stroke="var(--bg-0)" strokeWidth={strokeW}
                onMouseEnter={() => setHoverTrade(it.i)}
                onMouseLeave={() => setHoverTrade(null)}
                onClick={() => onTradeSelect && onTradeSelect(it.i === selectedTrade ? null : it.i)}
              />
              {/* Exit marker */}
              {it.xx != null && it.xy != null && (
                <circle
                  cx={it.xx} cy={it.xy} r={exitR}
                  fill={winColor} stroke="var(--bg-0)" strokeWidth={strokeW}
                  onMouseEnter={() => setHoverTrade(it.i)}
                  onMouseLeave={() => setHoverTrade(null)}
                  onClick={() => onTradeSelect && onTradeSelect(it.i === selectedTrade ? null : it.i)}
                />
              )}
              {/* Active outline ring */}
              {isActive && (
                <>
                  <circle cx={it.ex} cy={it.ey} r={entryR + 1.4} fill="none" stroke="var(--info)" strokeWidth="0.3" opacity="0.8" />
                  {it.xx != null && it.xy != null && (
                    <circle cx={it.xx} cy={it.xy} r={exitR + 1.4} fill="none" stroke={winColor} strokeWidth="0.3" opacity="0.8" />
                  )}
                </>
              )}
            </g>
          );
        })}

        {/* Crosshair */}
        {hover !== null && (
          <g pointerEvents="none">
            <line x1={x(hover)} x2={x(hover)} y1="0" y2={H} stroke="var(--accent)" strokeWidth="0.5" strokeDasharray="2,2" />
            <circle cx={x(hover)} cy={y(current.close)} r="1.8" fill="var(--accent)" />
          </g>
        )}
      </svg>

      {/* Y-axis labels */}
      <div style={{ position: 'absolute', right: 8, top: 4, fontSize: 9, color: 'var(--text-3)', fontFamily: 'var(--font-mono)' }}>${(Number(yMax) || 0).toFixed(2)}</div>
      <div style={{ position: 'absolute', right: 8, top: `${(chartH / H) * 100}%`, transform: 'translateY(-100%)', fontSize: 9, color: 'var(--text-3)', fontFamily: 'var(--font-mono)' }}>${(Number(yMin) || 0).toFixed(2)}</div>

      {/* Legend - trade colors */}
      {tradeGeom.items.length > 0 && (
        <div style={{ position: 'absolute', bottom: 6, right: 8, display: 'flex', gap: 10, fontSize: 10, color: 'var(--text-3)', fontFamily: 'var(--font-mono)', pointerEvents: 'none' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--info)' }} /> entry
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--pos)' }} /> win exit
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--neg)' }} /> loss exit
          </span>
        </div>
      )}
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
