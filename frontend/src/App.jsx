import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';
import { AreaChart, Area, BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { Play, TrendingUp, BarChart2, Activity, Settings, Zap } from 'lucide-react';

const STORAGE_KEY = 'spy_backtest_config';

const DEFAULT_CONFIG = {
  ticker: "SPY",
  years_history: 2,
  capital_allocation: 10000.0,
  use_dynamic_sizing: false,
  risk_percent: 5.0,
  max_trade_cap: 0,
  contracts_per_trade: 1,
  spread_cost_target: 250.0,
  strategy_type: "bull_call",
  entry_red_days: 2,
  exit_green_days: 2,
  target_dte: 14,
  stop_loss_pct: 50,
  commission_per_contract: 0.65,
  use_rsi_filter: true,
  rsi_threshold: 30,
  use_ema_filter: true,
  ema_length: 10,
  use_sma200_filter: false,
  use_volume_filter: false,
  use_mark_to_market: true,
  enable_mc_histogram: true,
  enable_walk_forward: false,
  walk_forward_windows: 4,
};

function loadConfig() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return { ...DEFAULT_CONFIG, ...JSON.parse(saved) };
  } catch {}
  return DEFAULT_CONFIG;
}

export default function App() {
  const [config, setConfig] = useState(loadConfig);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [apiError, setApiError] = useState(null);
  // Optimizer state
  const [showOptimizer, setShowOptimizer] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizerResult, setOptimizerResult] = useState(null);
  const [optParamX, setOptParamX] = useState('entry_red_days');
  const [optParamY, setOptParamY] = useState('target_dte');

  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersPluginRef = useRef(null);

  // ── Chart init ──
  useEffect(() => {
    if (!chartContainerRef.current) return;
    const chart = createChart(chartContainerRef.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#8b8b9d' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.1)', timeVisible: true },
    });
    chartRef.current = chart;
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#48bb78', downColor: '#f56565', borderVisible: false,
      wickUpColor: '#48bb78', wickDownColor: '#f56565',
    });
    seriesRef.current = series;
    markersPluginRef.current = createSeriesMarkers(series, []);
    return () => { chart.remove(); chartRef.current = null; seriesRef.current = null; };
  }, []);

  // ── Push data ──
  useEffect(() => {
    if (!result || !seriesRef.current || !chartRef.current) return;
    const prices = (result.price_history || []).filter(p => p.open != null && p.high != null && p.low != null && p.close != null);
    seriesRef.current.setData(prices);
    const markers = [];
    (result.trades || []).forEach((t) => {
      if (t.entry_date) markers.push({ time: t.entry_date, position: 'belowBar', color: '#48bb78', shape: 'arrowUp', text: config.strategy_type === 'bear_put' ? 'PUT' : 'BUY' });
      if (t.exit_date) {
        const color = t.win ? '#48bb78' : '#f56565';
        const text = t.stopped_out ? 'STOP' : t.expired ? 'EXP' : t.win ? 'WIN' : 'LOSS';
        markers.push({ time: t.exit_date, position: 'aboveBar', color, shape: 'arrowDown', text });
      }
    });
    markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    if (markersPluginRef.current) markersPluginRef.current.setMarkers(markers);
    chartRef.current.timeScale().fitContent();
  }, [result]);

  // ── API ──
  const runSimulation = useCallback(async () => {
    setLoading(true); setApiError(null);
    try {
      const res = await fetch('http://127.0.0.1:8000/api/backtest', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) });
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setResult(json);
    } catch (e) { setApiError(e.message || 'Failed to connect.'); console.error(e); }
    finally { setLoading(false); }
  }, [config]);

  const runOptimizer = useCallback(async () => {
    setOptimizing(true);
    try {
      const paramRanges = {
        entry_red_days: [1, 2, 3, 4, 5],
        exit_green_days: [1, 2, 3, 4],
        target_dte: [7, 14, 21, 30, 45],
        stop_loss_pct: [25, 50, 75, 100],
        spread_cost_target: [100, 200, 300, 400, 500],
      };
      const res = await fetch('http://127.0.0.1:8000/api/optimize', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_config: config,
          param_x: optParamX, param_y: optParamY,
          x_values: paramRanges[optParamX] || [1,2,3,4],
          y_values: paramRanges[optParamY] || [7,14,21,30],
        }),
      });
      const json = await res.json();
      setOptimizerResult(json);
    } catch (e) { console.error(e); }
    finally { setOptimizing(false); }
  }, [config, optParamX, optParamY]);

  useEffect(() => { runSimulation(); }, []);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setConfig((prev) => {
      const next = { ...prev, [name]: type === 'checkbox' ? checked : isNaN(Number(value)) ? value : value === '' ? value : Number(value) };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  };

  const m = result?.metrics ?? {};

  return (
    <div className="app-container">
      {/* ── Sidebar ── */}
      <div className="sidebar">
        <div className="sidebar-header">
          <h1><Activity size={26} /> Neural Backtester</h1>
          <p style={{ fontSize: '0.78rem', color: '#8b8b9d', marginTop: '6px' }}>
            Black-Scholes Options Spread Engine
          </p>
        </div>

        <Section label="Strategy">
          <div style={{ display: 'flex', gap: '8px', marginBottom: 12 }}>
            {['bull_call', 'bear_put'].map(s => (
              <button key={s} onClick={() => handleChange({ target: { name: 'strategy_type', value: s, type: 'text' } })}
                style={{
                  flex: 1, padding: '10px', borderRadius: 8, border: `1px solid ${config.strategy_type === s ? '#6b46c1' : 'rgba(255,255,255,0.08)'}`,
                  background: config.strategy_type === s ? 'rgba(107,70,193,0.2)' : 'var(--bg-card)',
                  color: config.strategy_type === s ? '#a78bfa' : '#8b8b9d', fontWeight: 600, fontSize: '0.8rem',
                  cursor: 'pointer', transition: 'all 0.2s', textTransform: 'uppercase',
                }}>
                {s === 'bull_call' ? '🟢 Bull Call' : '🔴 Bear Put'}
              </button>
            ))}
          </div>
        </Section>

        <Section label="Underlying">
          <Field label="Ticker"><input type="text" name="ticker" value={config.ticker} onChange={handleChange} /></Field>
          <Field label="History (years)"><input type="number" name="years_history" min={1} max={10} value={config.years_history} onChange={handleChange} /></Field>
        </Section>

        <Section label="Capital Sizing">
          <Field label="Capital ($)"><input type="number" name="capital_allocation" value={config.capital_allocation} onChange={handleChange} /></Field>
          <Toggle name="use_dynamic_sizing" label="Dynamic % Sizing" checked={config.use_dynamic_sizing} onChange={handleChange} />
          {config.use_dynamic_sizing ? (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
              <Field label="% Risk / Trade"><input type="number" name="risk_percent" step="0.5" min="0.5" max="100" value={config.risk_percent} onChange={handleChange} /></Field>
              <Field label="Max Cap ($)"><input type="number" name="max_trade_cap" min="0" value={config.max_trade_cap} onChange={handleChange} /></Field>
            </div>
          ) : (
            <Field label="Fixed Contracts"><input type="number" name="contracts_per_trade" min={1} value={config.contracts_per_trade} onChange={handleChange} /></Field>
          )}
          <Field label="Spread Target ($)"><input type="number" name="spread_cost_target" value={config.spread_cost_target} onChange={handleChange} /></Field>
          <Field label="Commission / Contract ($)"><input type="number" name="commission_per_contract" step="0.01" min="0" value={config.commission_per_contract} onChange={handleChange} /></Field>
        </Section>

        <Section label="Entry / Exit">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
            <Field label={config.strategy_type === 'bear_put' ? 'Green Days' : 'Red Days'}><input type="number" name="entry_red_days" min={1} value={config.entry_red_days} onChange={handleChange} /></Field>
            <Field label={config.strategy_type === 'bear_put' ? 'Red Days' : 'Green Days'}><input type="number" name="exit_green_days" min={1} value={config.exit_green_days} onChange={handleChange} /></Field>
            <Field label="Target DTE"><input type="number" name="target_dte" min={1} value={config.target_dte} onChange={handleChange} /></Field>
            <Field label="Stop Loss %"><input type="number" name="stop_loss_pct" min={0} max={100} value={config.stop_loss_pct} onChange={handleChange} /></Field>
          </div>
        </Section>

        <Section label="Filters">
          <Toggle name="use_rsi_filter" label="RSI Filter" checked={config.use_rsi_filter} onChange={handleChange} />
          {config.use_rsi_filter && <Field label={`RSI Threshold (${config.rsi_threshold})`} style={{ marginTop: 8 }}><input type="number" name="rsi_threshold" min={10} max={50} value={config.rsi_threshold} onChange={handleChange} /></Field>}
          <Toggle name="use_ema_filter" label="EMA Pullback" checked={config.use_ema_filter} onChange={handleChange} />
          {config.use_ema_filter && <Field label={`EMA Length (${config.ema_length})`} style={{ marginTop: 8 }}><input type="number" name="ema_length" min={5} max={200} value={config.ema_length} onChange={handleChange} /></Field>}
          <Toggle name="use_sma200_filter" label="SMA 200" checked={config.use_sma200_filter} onChange={handleChange} />
          <Toggle name="use_volume_filter" label="Volume Spike" checked={config.use_volume_filter} onChange={handleChange} />
        </Section>

        <Section label="Advanced Features">
          <Toggle name="use_mark_to_market" label="Mark-to-Market" checked={config.use_mark_to_market} onChange={handleChange} />
          <Toggle name="enable_mc_histogram" label="MC Histogram" checked={config.enable_mc_histogram} onChange={handleChange} />
          <Toggle name="enable_walk_forward" label="Walk-Forward" checked={config.enable_walk_forward} onChange={handleChange} />
          {config.enable_walk_forward && (
            <Field label="Windows" style={{ marginTop: 8 }}><input type="number" name="walk_forward_windows" min={2} max={12} value={config.walk_forward_windows} onChange={handleChange} /></Field>
          )}
          <div style={{ marginTop: 8 }}>
            <button className="btn-secondary" onClick={() => setShowOptimizer(!showOptimizer)} style={{ width: '100%' }}>
              <Settings size={16} /> {showOptimizer ? 'Hide Optimizer' : 'Parameter Optimizer'}
            </button>
          </div>
        </Section>

        <button className="btn-primary" onClick={runSimulation} disabled={loading} id="run-simulation-btn">
          {loading ? <><div className="spinner" style={{ width: 18, height: 18, borderWidth: 3 }} /> Running…</> : <><Play size={18} /> Run Simulation</>}
        </button>
      </div>

      {/* ── Main ── */}
      <div className="main-content">
        {apiError && <div className="error-banner">⚠ {apiError}</div>}

        {/* Strategy indicator */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: -8 }}>
          <span style={{ fontSize: '0.8rem', color: '#8b8b9d' }}>Strategy:</span>
          <span style={{
            padding: '4px 12px', borderRadius: 6, fontSize: '0.75rem', fontWeight: 700,
            background: config.strategy_type === 'bull_call' ? 'rgba(72,187,120,0.15)' : 'rgba(245,101,101,0.15)',
            color: config.strategy_type === 'bull_call' ? '#48bb78' : '#f56565',
          }}>
            {config.strategy_type === 'bull_call' ? '🟢 BULL CALL SPREAD' : '🔴 BEAR PUT SPREAD'}
          </span>
          {config.use_mark_to_market && <span style={{ padding: '4px 8px', borderRadius: 6, fontSize: '0.7rem', background: 'rgba(167,139,250,0.15)', color: '#a78bfa' }}>MTM</span>}
        </div>

        {/* Metrics Grid */}
        <div className="metrics-grid">
          <MetricCard title="Total P&L" value={`${(m.total_pnl??0) >= 0 ? '+' : ''}$${(m.total_pnl??0).toLocaleString()}`} color={(m.total_pnl??0) >= 0 ? '#48bb78' : '#f56565'} />
          <MetricCard title="Win Rate" value={`${m.win_rate??0}%`} />
          <MetricCard title="Total Trades" value={m.total_trades??0} />
          <MetricCard title="Final Equity" value={`$${(m.final_equity??config.capital_allocation).toLocaleString()}`} color="#a78bfa" />
          <MetricCard title="Profit Factor" value={m.profit_factor??0} color={(m.profit_factor??0) >= 1.5 ? '#48bb78' : '#f56565'} />
          <MetricCard title="Max Consec. Loss" value={m.max_consec_losses??0} color="#f56565" />
          <MetricCard title="Recovery Factor" value={m.recovery_factor??0} />
          <MetricCard title="Max Drawdown" value={`${m.max_drawdown??0}%`} color="#f56565" />
          <MetricCard title="Sharpe" value={m.sharpe_ratio??0} />
          <MetricCard title="Sortino" value={m.sortino_ratio??0} />
          <MetricCard title="Avg P&L" value={`${(m.avg_pnl??0) >= 0 ? '+' : ''}$${m.avg_pnl??0}`} color={(m.avg_pnl??0) >= 0 ? '#48bb78' : '#f56565'} />
          <MetricCard title="Avg Hold" value={`${m.avg_hold_days??0}d`} />
        </div>

        {/* Charts */}
        <div className="charts-layout">
          <div className="chart-box" style={{ position: 'relative' }}>
            {loading && <div className="loader-overlay"><div className="spinner" /></div>}
            <h3><BarChart2 size={18} /> Price Action &amp; Signals</h3>
            <div ref={chartContainerRef} style={{ width: '100%', height: '420px' }} />
          </div>

          <div className="chart-box">
            <h3><TrendingUp size={18} /> Equity {config.use_mark_to_market ? '(MTM)' : ''} &amp; Drawdown</h3>
            {result?.equity_curve?.length ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <ResponsiveContainer width="100%" height={260}>
                  <AreaChart data={result.equity_curve} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                    <defs><linearGradient id="eq" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#6b46c1" stopOpacity={0.5} /><stop offset="95%" stopColor="#6b46c1" stopOpacity={0} /></linearGradient></defs>
                    <XAxis dataKey="date" hide /><YAxis domain={['auto', 'auto']} hide />
                    <Tooltip contentStyle={{ background: '#20203a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, color: '#fff' }} formatter={(v) => [`$${v.toLocaleString()}`, 'Equity']} />
                    <Area type="monotone" dataKey="equity" stroke="#a78bfa" strokeWidth={2.5} fill="url(#eq)" />
                  </AreaChart>
                </ResponsiveContainer>
                <ResponsiveContainer width="100%" height={150}>
                  <AreaChart data={result.equity_curve} margin={{ top: 0, right: 8, bottom: 0, left: 8 }}>
                    <defs><linearGradient id="dd" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#f56565" stopOpacity={0.5} /><stop offset="95%" stopColor="#f56565" stopOpacity={0} /></linearGradient></defs>
                    <XAxis dataKey="date" hide /><YAxis domain={['dataMin', 0]} hide />
                    <Tooltip contentStyle={{ background: '#20203a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, color: '#fff' }} formatter={(v) => [`${v}%`, 'Drawdown']} />
                    <Area type="monotone" dataKey="drawdown" stroke="#f56565" strokeWidth={1.5} fill="url(#dd)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 420, color: '#8b8b9d' }}>
                {loading ? 'Calculating…' : 'Run a simulation to see results.'}
              </div>
            )}
          </div>
        </div>

        {/* Phase 5 Analytics */}
        <div className="charts-layout" style={{ marginTop: '24px', minHeight: 'auto' }}>
           <HeatmapComponent data={result?.heatmap} />
           <MonteCarloComponent data={result?.monte_carlo} showHistogram={config.enable_mc_histogram} />
        </div>

        {/* MC Histogram */}
        {config.enable_mc_histogram && result?.monte_carlo?.distribution?.length > 0 && (
          <div className="chart-box" style={{ marginTop: 24 }}>
            <h3><BarChart2 size={18} /> Monte Carlo Distribution (1,000 sims)</h3>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={result.monte_carlo.distribution} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                <XAxis dataKey="bin" tick={{ fill: '#8b8b9d', fontSize: 10 }} tickFormatter={v => `$${(v/1000).toFixed(1)}k`} />
                <YAxis hide />
                <Tooltip contentStyle={{ background: '#20203a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, color: '#fff' }}
                  formatter={(v, name, props) => [v, 'Simulations']}
                  labelFormatter={v => `Final Equity: $${Number(v).toLocaleString()}`} />
                <Bar dataKey="count" radius={[4,4,0,0]}>
                  {result.monte_carlo.distribution.map((entry, idx) => (
                    <Cell key={idx} fill={entry.profitable ? 'rgba(72,187,120,0.7)' : 'rgba(245,101,101,0.7)'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Walk-Forward Results */}
        {config.enable_walk_forward && result?.walk_forward?.length > 0 && (
          <div className="chart-box" style={{ marginTop: 24 }}>
            <h3><Zap size={18} /> Walk-Forward Validation ({result.walk_forward.length} windows)</h3>
            <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(result.walk_forward.length, 6)}, 1fr)`, gap: 12, marginTop: 16 }}>
              {result.walk_forward.map((w, i) => (
                <div key={i} style={{
                  background: w.profitable ? 'rgba(72,187,120,0.1)' : 'rgba(245,101,101,0.1)',
                  border: `1px solid ${w.profitable ? 'rgba(72,187,120,0.3)' : 'rgba(245,101,101,0.3)'}`,
                  borderRadius: 10, padding: 16, textAlign: 'center',
                }}>
                  <div style={{ fontSize: '0.7rem', color: '#8b8b9d', marginBottom: 4 }}>W{w.window}</div>
                  <div style={{ fontSize: '0.65rem', color: '#8b8b9d', marginBottom: 8 }}>{w.start_date}<br/>→ {w.end_date}</div>
                  <div style={{ fontSize: '1.2rem', fontWeight: 700, color: w.profitable ? '#48bb78' : '#f56565' }}>
                    {w.pnl >= 0 ? '+' : ''}${w.pnl}
                  </div>
                  <div style={{ fontSize: '0.75rem', color: '#8b8b9d', marginTop: 4 }}>{w.trades} trades · {w.win_rate}% WR</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Optimizer Panel */}
        {showOptimizer && (
          <div className="chart-box" style={{ marginTop: 24 }}>
            <h3><Settings size={18} /> Parameter Optimizer</h3>
            <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end', marginBottom: 16, flexWrap: 'wrap' }}>
              <div>
                <label style={{ fontSize: '0.75rem', color: '#8b8b9d', display: 'block', marginBottom: 4 }}>X-Axis Parameter</label>
                <select value={optParamX} onChange={e => setOptParamX(e.target.value)}
                  style={{ background: 'var(--bg-card)', color: '#f0f0f5', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: '8px 12px', fontSize: '0.85rem' }}>
                  {['entry_red_days','exit_green_days','target_dte','stop_loss_pct','spread_cost_target'].map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div>
                <label style={{ fontSize: '0.75rem', color: '#8b8b9d', display: 'block', marginBottom: 4 }}>Y-Axis Parameter</label>
                <select value={optParamY} onChange={e => setOptParamY(e.target.value)}
                  style={{ background: 'var(--bg-card)', color: '#f0f0f5', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: '8px 12px', fontSize: '0.85rem' }}>
                  {['entry_red_days','exit_green_days','target_dte','stop_loss_pct','spread_cost_target'].map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <button onClick={runOptimizer} disabled={optimizing}
                style={{ padding: '8px 20px', borderRadius: 8, border: 'none', background: '#6b46c1', color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: '0.85rem' }}>
                {optimizing ? 'Running…' : '⚡ Optimize'}
              </button>
            </div>
            {optimizerResult?.results && <OptimizerHeatmap data={optimizerResult} />}
          </div>
        )}

        {/* Trade log */}
        <div className="table-container">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 20px 0 20px' }}>
            <h3 style={{ fontSize: '1.1rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8 }}>
              <BarChart2 size={18} /> Trade Log
            </h3>
            {result?.trades?.length > 0 && (
              <button onClick={() => {
                  const headers = ['Entry','Exit','SPY Entry','SPY Exit','Cost','Exit Val','P&L','Contracts','Days Held','Commission','Status'];
                  const rows = result.trades.map(t => [t.entry_date, t.exit_date, t.entry_spy, t.exit_spy, t.spread_cost, t.spread_exit, t.pnl, t.contracts||'-', t.days_held||'-', t.commission||0, t.stopped_out?'STOP LOSS':t.expired?'EXPIRED':t.win?'PROFIT':'LOSS']);
                  const csv = [headers,...rows].map(r => r.join(',')).join('\n');
                  const blob = new Blob([csv], { type: 'text/csv' }); const url = URL.createObjectURL(blob);
                  const a = document.createElement('a'); a.href = url; a.download = 'trades.csv'; a.click(); URL.revokeObjectURL(url);
                }}
                style={{ background: 'rgba(107,70,193,0.15)', color: '#a78bfa', border: '1px solid rgba(107,70,193,0.3)', borderRadius: 6, padding: '6px 14px', fontSize: '0.8rem', fontWeight: 600, cursor: 'pointer' }}>
                ⬇ Export CSV
              </button>
            )}
          </div>
          <table>
            <thead><tr>
              <th>Entry</th><th>Exit</th><th>SPY Entry</th><th>SPY Exit</th>
              <th>Cost</th><th>Exit Val</th><th>P&amp;L</th>
              <th>Contracts</th><th>Days</th><th>Comm.</th><th>Status</th>
            </tr></thead>
            <tbody>
              {result?.trades?.length ? result.trades.map((t, i) => (
                <tr key={i}>
                  <td>{t.entry_date}</td><td>{t.exit_date}</td>
                  <td>${t.entry_spy}</td><td>${t.exit_spy}</td>
                  <td>${t.spread_cost}</td><td>${t.spread_exit}</td>
                  <td style={{ color: t.pnl >= 0 ? '#48bb78' : '#f56565', fontWeight: 600 }}>{t.pnl >= 0 ? '+' : ''}${t.pnl}</td>
                  <td>{t.contracts || '-'}</td><td>{t.days_held || '-'}d</td>
                  <td style={{ color: '#8b8b9d' }}>${t.commission || '0'}</td>
                  <td>{t.stopped_out ? <span className="badge loss">STOP LOSS</span> : t.expired ? <span className="badge expired">EXPIRED</span> : t.win ? <span className="badge win">PROFIT</span> : <span className="badge loss">LOSS</span>}</td>
                </tr>
              )) : (
                <tr><td colSpan={11} style={{ textAlign: 'center', color: '#8b8b9d', padding: 32 }}>{loading ? 'Running…' : 'No trades found.'}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ── Helper Components ── */
function Section({ label, children }) {
  return (<div style={{ marginBottom: 20 }}><p style={{ fontSize: '0.75rem', fontWeight: 600, color: '#6b46c1', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>{label}</p>{children}</div>);
}
function Field({ label, children, style }) {
  return (<div className="form-group" style={style}><label style={{ fontSize: '0.82rem', color: '#8b8b9d', display: 'block', marginBottom: 6 }}>{label}</label>{children}</div>);
}
function Toggle({ name, label, checked, onChange }) {
  return (<div className="toggle-group"><label style={{ color: checked ? '#f0f0f5' : '#8b8b9d', transition: 'color .2s' }}>{label}</label><label className="switch"><input type="checkbox" name={name} checked={checked} onChange={onChange} /><span className="slider" /></label></div>);
}
function MetricCard({ title, value, color }) {
  return (<div className="metric-card"><span className="metric-title">{title}</span><span className="metric-value" style={{ color: color || '#f0f0f5' }}>{value}</span></div>);
}

function HeatmapComponent({ data }) {
  if (!data || data.length === 0) return null;
  const days = ["Mon","Tue","Wed","Thu","Fri"];
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return (
    <div className="chart-box">
       <h3><Activity size={18} /> Win Rate Heatmap</h3>
       <div style={{ display: 'grid', gridTemplateColumns: `auto repeat(12, 1fr)`, gap: '4px', marginTop: '16px', overflowX: 'auto' }}>
          <div />
          {months.map(m => <div key={m} style={{ textAlign: 'center', fontSize: '0.75rem', color: '#8b8b9d' }}>{m}</div>)}
          {days.map(d => (
             <React.Fragment key={d}>
                <div style={{ fontSize: '0.75rem', color: '#8b8b9d', alignSelf: 'center', paddingRight: '12px' }}>{d}</div>
                {months.map(m => {
                   const c = data.find(x => x.day === d && x.month === m);
                   const wr = c ? c.win_rate : null;
                   const bg = wr === null ? '#20203a' : wr > 50 ? `rgba(72,187,120,${Math.max(0.2,(wr-50)/50)})` : `rgba(245,101,101,${Math.max(0.2,(50-wr)/50)})`;
                   return (<div key={m} style={{ background: bg, borderRadius: '4px', height: '28px', width: '100%', minWidth: '24px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.65rem', color: wr === null ? '#8b8b9d' : '#fff' }}>{wr !== null ? `${Math.round(wr)}%` : '-'}</div>);
                })}
             </React.Fragment>
          ))}
       </div>
    </div>
  );
}

function MonteCarloComponent({ data, showHistogram }) {
   if (!data) return null;
   return (
     <div className="chart-box">
        <h3><BarChart2 size={18} /> Monte Carlo Simulation</h3>
        <p style={{ fontSize: '0.8rem', color: '#8b8b9d', marginBottom: '16px' }}>1,000 resampled trade sequences{showHistogram ? ' — histogram below' : ''}</p>
        <div style={{ display: 'flex', justifyContent: 'space-around', alignItems: 'center', height: '100%', padding: '20px 0', flexWrap: 'wrap', gap: 8 }}>
            <MCMetric label="P5 (Worst)" value={`$${data.p05?.toLocaleString()}`} color="#f56565" />
            <MCMetric label="P50 (Median)" value={`$${data.p50?.toLocaleString()}`} color="#f0f0f5" />
            <MCMetric label="Avg (EV)" value={`$${data.ev?.toLocaleString()}`} color="#a78bfa" />
            <MCMetric label="P95 (Best)" value={`$${data.p95?.toLocaleString()}`} color="#48bb78" />
            <MCMetric label="Win Prob" value={`${data.prob_profit}%`} color={data.prob_profit >= 50 ? '#48bb78' : '#f56565'} />
        </div>
     </div>
   );
}

function MCMetric({ label, value, color }) {
  return (
    <div style={{ textAlign: 'center', padding: '0 8px' }}>
      <div style={{ fontSize: '0.78rem', color, textTransform: 'uppercase', marginBottom: '6px' }}>{label}</div>
      <div style={{ fontSize: '1.3rem', fontWeight: 600 }}>{value}</div>
    </div>
  );
}

function OptimizerHeatmap({ data }) {
  if (!data?.results?.length) return null;
  const xs = [...new Set(data.results.map(r => r.x))].sort((a,b) => a-b);
  const ys = [...new Set(data.results.map(r => r.y))].sort((a,b) => a-b);
  const pnls = data.results.map(r => r.pnl);
  const maxPnl = Math.max(...pnls);
  const minPnl = Math.min(...pnls);

  const getColor = (pnl) => {
    if (maxPnl === minPnl) return 'rgba(167,139,250,0.3)';
    if (pnl >= 0) { const t = pnl / (maxPnl || 1); return `rgba(72,187,120,${Math.max(0.15, t * 0.9)})`; }
    const t = Math.abs(pnl) / (Math.abs(minPnl) || 1); return `rgba(245,101,101,${Math.max(0.15, t * 0.9)})`;
  };

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: '0.75rem', color: '#8b8b9d', marginBottom: 8 }}>P&L by <strong>{data.param_x}</strong> (→) × <strong>{data.param_y}</strong> (↓)</div>
      <div style={{ display: 'grid', gridTemplateColumns: `60px repeat(${xs.length}, 1fr)`, gap: 4 }}>
        <div />
        {xs.map(x => <div key={x} style={{ textAlign: 'center', fontSize: '0.72rem', color: '#a78bfa', fontWeight: 600 }}>{x}</div>)}
        {ys.map(y => (
          <React.Fragment key={y}>
            <div style={{ fontSize: '0.72rem', color: '#a78bfa', fontWeight: 600, display: 'flex', alignItems: 'center' }}>{y}</div>
            {xs.map(x => {
              const cell = data.results.find(r => r.x === x && r.y === y);
              const pnl = cell?.pnl ?? 0;
              return (
                <div key={x} style={{
                  background: getColor(pnl), borderRadius: 6, padding: '8px 4px', textAlign: 'center',
                  fontSize: '0.72rem', fontWeight: 600, color: pnl >= 0 ? '#48bb78' : '#f56565',
                }} title={`${cell?.trades} trades, ${cell?.win_rate}% WR`}>
                  {pnl >= 0 ? '+' : ''}${pnl}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
