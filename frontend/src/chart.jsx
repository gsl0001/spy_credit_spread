import { useRef, useMemo, useEffect } from 'react';
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';

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
  const containerRef = useRef();
  const chartRef = useRef();
  const seriesRef = useRef();
  const markersRef = useRef();

  useEffect(() => {
    if (!containerRef.current) return;
    
    let chart;
    try {
      chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth || 600,
        height: containerRef.current.clientHeight || 400,
        layout: {
          background: { type: 'solid', color: 'transparent' },
          textColor: '#888',
        },
        grid: {
          vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
          horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
        },
        rightPriceScale: { borderColor: 'rgba(255, 255, 255, 0.1)' },
        timeScale: { borderColor: 'rgba(255, 255, 255, 0.1)', timeVisible: true },
      });

      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderVisible: false,
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
      });

      // Create markers plugin for trade arrows
      const markerPlugin = createSeriesMarkers(candleSeries);

      chartRef.current = chart;
      seriesRef.current = candleSeries;
      markersRef.current = markerPlugin;

      chart.subscribeClick(() => {
        if (onTradeSelect) onTradeSelect(null);
      });

    } catch (e) {
      console.error("Lightweight charts init error:", e);
      return;
    }

    const handleResize = () => {
      if (containerRef.current && chartRef.current) {
        const w = containerRef.current.clientWidth;
        const h = containerRef.current.clientHeight;
        if (w > 0 && h > 0) {
          chartRef.current.applyOptions({ width: w, height: h });
        }
      }
    };

    const resizeObserver = new ResizeObserver(() => {
      handleResize();
    });
    resizeObserver.observe(containerRef.current);
    
    // Fallback timer just in case
    setTimeout(handleResize, 100);

    return () => {
      resizeObserver.disconnect();
      if (chartRef.current) {
        try { chartRef.current.remove(); } catch (e) {}
      }
    };
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current || !series || series.length === 0) return;
    
    try {
      const formattedData = series.map(d => {
        const [yyyy, mm, dd] = d.time.split('-').map(Number);
        const t = Math.floor(Date.UTC(yyyy, mm - 1, dd) / 1000);
        return {
          time: t,
          originalTime: d.time,
          open: Number(d.open),
          high: Number(d.high),
          low: Number(d.low),
          close: Number(d.close),
        };
      }).filter(d => !isNaN(d.time) && !isNaN(d.open) && !isNaN(d.close))
        .sort((a, b) => a.time - b.time);

      const deduped = [];
      const seen = new Set();
      const timeMap = new Map();
      for (let d of formattedData) {
        if (!seen.has(d.time)) {
          seen.add(d.time);
          timeMap.set(d.originalTime, d.time);
          deduped.push({ time: d.time, open: d.open, high: d.high, low: d.low, close: d.close });
        }
      }

      seriesRef.current.setData(deduped);
      chartRef.current.timeScale().fitContent();

      if (markersRef.current) {
        const markers = [];
        
        trades.forEach((t, i) => {
          const isSelected = selectedTrade === i;
          if (t.entry_date && timeMap.has(t.entry_date)) {
            markers.push({
              time: timeMap.get(t.entry_date),
              position: t.side === 'BUY' ? 'belowBar' : 'aboveBar',
              color: '#2962FF',
              shape: t.side === 'BUY' ? 'arrowUp' : 'arrowDown',
              text: isSelected ? `[${t.side}] Entry` : (t.side || ''),
            });
          }
          
          if (t.exit_date && timeMap.has(t.exit_date)) {
            const win = (t.pnl || 0) > 0;
            markers.push({
              time: timeMap.get(t.exit_date),
              position: win ? 'aboveBar' : 'belowBar',
              color: win ? '#26a69a' : '#ef5350',
              shape: win ? 'arrowDown' : 'arrowUp',
              text: isSelected ? (win ? 'WIN' : 'LOSS') : (win ? 'Win' : 'Loss'),
            });
          }
        });

        markers.sort((a, b) => a.time - b.time);
        markersRef.current.setMarkers(markers);
      }
    } catch (err) {
      console.error("Lightweight charts render error:", err);
    }
  }, [series, trades, selectedTrade]);

  return (
    <div style={{ position: 'relative', width: '100%', height }}>
      {(!series || series.length < 2) && (
        <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: 'var(--text-3)', fontSize: 12, zIndex: 10 }}>
          No price history — run a simulation
        </div>
      )}
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
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
