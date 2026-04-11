import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { Play, TrendingUp, BarChart2, Activity } from 'lucide-react';

const DEFAULT_CONFIG = {
  ticker: "SPY",
  years_history: 2,
  capital_allocation: 10000.0,
  use_dynamic_sizing: false,
  risk_percent: 5.0,
  max_trade_cap: 0,
  contracts_per_trade: 1,
  spread_cost_target: 250.0,
  entry_red_days: 2,
  exit_green_days: 2,
  target_dte: 14,
  stop_loss_pct: 50,
  use_rsi_filter: true,
  rsi_threshold: 30,
  use_ema_filter: true,
  ema_length: 10,
  use_sma200_filter: false,
  use_volume_filter: false,
};

export default function App() {
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [apiError, setApiError] = useState(null);

  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersPluginRef = useRef(null);

  // ── Chart initialisation / teardown ──────────────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: '#8b8b9d',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.04)' },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.1)', timeVisible: true },
    });
    chartRef.current = chart;

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#48bb78',
      downColor: '#f56565',
      borderVisible: false,
      wickUpColor: '#48bb78',
      wickDownColor: '#f56565',
    });
    seriesRef.current = series;
    // Initialise the markers plugin once (v5 API)
    markersPluginRef.current = createSeriesMarkers(series, []);

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []); // run exactly once

  // ── Push new data into the chart whenever result changes ──────────────────
  useEffect(() => {
    if (!result || !seriesRef.current || !chartRef.current) return;

    const prices = (result.price_history || []).filter(
      (p) => p.open != null && p.high != null && p.low != null && p.close != null
    );
    seriesRef.current.setData(prices);

    const markers = [];
    (result.trades || []).forEach((t) => {
      if (t.entry_date) {
        markers.push({
          time: t.entry_date,
          position: 'belowBar',
          color: '#48bb78',
          shape: 'arrowUp',
          text: 'BUY',
        });
      }
      if (t.exit_date) {
        const color = t.win ? '#48bb78' : '#f56565';
        const text = t.stopped_out ? 'STOP' : t.expired ? 'EXP' : t.win ? 'WIN' : 'LOSS';
        markers.push({
          time: t.exit_date,
          position: 'aboveBar',
          color,
          shape: 'arrowDown',
          text,
        });
      }
    });

    markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    // v5 API: use the markers plugin instead of series.setMarkers()
    if (markersPluginRef.current) {
      markersPluginRef.current.setMarkers(markers);
    }
    chartRef.current.timeScale().fitContent();
  }, [result]);

  // ── API call ──────────────────────────────────────────────────────────────
  const runSimulation = useCallback(async () => {
    setLoading(true);
    setApiError(null);
    try {
      const res = await fetch('http://127.0.0.1:8000/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setResult(json);
    } catch (e) {
      setApiError(e.message || 'Failed to connect to backend.');
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [config]);

  // Auto-run on mount
  useEffect(() => {
    runSimulation();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setConfig((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : isNaN(Number(value)) ? value : value === '' ? value : Number(value),
    }));
  };

  const metrics = result?.metrics ?? {};
  const pnl = metrics.total_pnl ?? 0;
  const winRate = metrics.win_rate ?? 0;
  const totalTrades = metrics.total_trades ?? 0;
  const finalEquity = metrics.final_equity ?? config.capital_allocation;
  
  const avgPnl = metrics.avg_pnl ?? 0;
  const avgHold = metrics.avg_hold_days ?? 0;
  const sharpe = metrics.sharpe_ratio ?? 0;
  const sortino = metrics.sortino_ratio ?? 0;
  const maxDd = metrics.max_drawdown ?? 0;

  return (
    <div className="app-container">
      {/* ── Sidebar ── */}
      <div className="sidebar">
        <div className="sidebar-header">
          <h1><Activity size={26} /> Neural Backtester</h1>
          <p style={{ fontSize: '0.78rem', color: '#8b8b9d', marginTop: '6px' }}>
            Black-Scholes Bull Call Spread Engine
          </p>
        </div>

        <Section label="Underlying">
          <Field label="Ticker">
            <input type="text" name="ticker" value={config.ticker} onChange={handleChange} />
          </Field>
          <Field label="History (years)">
            <input type="number" name="years_history" min={1} max={10} value={config.years_history} onChange={handleChange} />
          </Field>
        </Section>

        <Section label="Capital Sizing">
          <Field label="Capital ($)">
            <input type="number" name="capital_allocation" value={config.capital_allocation} onChange={handleChange} />
          </Field>
          
          <Toggle name="use_dynamic_sizing" label="Dynamic % Sizing" checked={config.use_dynamic_sizing} onChange={handleChange} />
          
          {config.use_dynamic_sizing ? (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
              <Field label="% Risk / Trade">
                <input type="number" name="risk_percent" step="0.5" min="0.5" max="100" value={config.risk_percent} onChange={handleChange} />
              </Field>
              <Field label="Max Cap ($) (0=None)">
                <input type="number" name="max_trade_cap" min="0" value={config.max_trade_cap} onChange={handleChange} />
              </Field>
            </div>
          ) : (
            <Field label="Fixed Contracts">
              <input type="number" name="contracts_per_trade" min={1} value={config.contracts_per_trade} onChange={handleChange} />
            </Field>
          )}

          <Field label="Spread Target ($)">
            <input type="number" name="spread_cost_target" value={config.spread_cost_target} onChange={handleChange} />
          </Field>
        </Section>

        <Section label="Entry / Exit">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
            <Field label="Red Days">
              <input type="number" name="entry_red_days" min={1} value={config.entry_red_days} onChange={handleChange} />
            </Field>
            <Field label="Green Days">
              <input type="number" name="exit_green_days" min={1} value={config.exit_green_days} onChange={handleChange} />
            </Field>
            <Field label="Target DTE">
              <input type="number" name="target_dte" min={1} value={config.target_dte} onChange={handleChange} />
            </Field>
            <Field label="Stop Loss %">
              <input type="number" name="stop_loss_pct" min={0} max={100} value={config.stop_loss_pct} onChange={handleChange} />
            </Field>
          </div>
        </Section>

        <Section label="Filters">
          <Toggle name="use_rsi_filter" label="RSI Oversold" checked={config.use_rsi_filter} onChange={handleChange} />
          {config.use_rsi_filter && (
            <Field label={`RSI Threshold (< ${config.rsi_threshold})`} style={{ marginTop: '8px' }}>
              <input type="number" name="rsi_threshold" min={10} max={50} value={config.rsi_threshold} onChange={handleChange} />
            </Field>
          )}
          <Toggle name="use_ema_filter" label="EMA Pullback" checked={config.use_ema_filter} onChange={handleChange} />
          {config.use_ema_filter && (
            <Field label={`EMA Length (${config.ema_length})`} style={{ marginTop: '8px' }}>
              <input type="number" name="ema_length" min={5} max={200} value={config.ema_length} onChange={handleChange} />
            </Field>
          )}
          <Toggle name="use_sma200_filter" label="SMA 200 Bull" checked={config.use_sma200_filter} onChange={handleChange} />
          <Toggle name="use_volume_filter" label="Volume Spike" checked={config.use_volume_filter} onChange={handleChange} />
        </Section>

        <button className="btn-primary" onClick={runSimulation} disabled={loading} id="run-simulation-btn">
          {loading
            ? <><div className="spinner" style={{ width: 18, height: 18, borderWidth: 3 }} /> Running…</>
            : <><Play size={18} /> Run Simulation</>}
        </button>
      </div>

      {/* ── Main ── */}
      <div className="main-content">
        {apiError && (
          <div className="error-banner">⚠ {apiError}</div>
        )}

        {/* Metrics Grid */}
        <div className="metrics-grid">
          <MetricCard title="Total P&L" value={`${pnl >= 0 ? '+' : ''}$${pnl.toLocaleString()}`} color={pnl >= 0 ? '#48bb78' : '#f56565'} />
          <MetricCard title="Win Rate" value={`${winRate}%`} />
          <MetricCard title="Total Trades" value={totalTrades} />
          <MetricCard title="Final Equity" value={`$${finalEquity.toLocaleString()}`} color="#a78bfa" />
          
          <MetricCard title="Avg P&L / Trade" value={`${avgPnl >= 0 ? '+' : ''}$${avgPnl}`} color={avgPnl >= 0 ? '#48bb78' : '#f56565'} />
          <MetricCard title="Avg Hold Time" value={`${avgHold} days`} />
          <MetricCard title="Max Drawdown" value={`${maxDd}%`} color="#f56565" />
          <MetricCard title="Sharpe Ratio" value={sharpe} />
          <MetricCard title="Sortino Ratio" value={sortino} />
        </div>

        {/* Charts */}
        <div className="charts-layout">
          <div className="chart-box" style={{ position: 'relative' }}>
            {loading && <div className="loader-overlay"><div className="spinner" /></div>}
            <h3><BarChart2 size={18} /> Price Action &amp; Signals</h3>
            <div ref={chartContainerRef} style={{ width: '100%', height: '420px' }} />
          </div>

          <div className="chart-box">
            <h3><TrendingUp size={18} /> Equity Curve & Drawdown</h3>
            {result?.equity_curve?.length ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <ResponsiveContainer width="100%" height={260}>
                  <AreaChart data={result.equity_curve} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
                    <defs>
                      <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#6b46c1" stopOpacity={0.5} />
                        <stop offset="95%" stopColor="#6b46c1" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="date" hide />
                    <YAxis domain={['auto', 'auto']} hide />
                    <Tooltip
                      contentStyle={{ background: '#20203a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, color: '#fff' }}
                      formatter={(v) => [`$${v.toLocaleString()}`, 'Equity']}
                    />
                    <Area type="monotone" dataKey="equity" stroke="#a78bfa" strokeWidth={2.5} fill="url(#eq)" />
                  </AreaChart>
                </ResponsiveContainer>
                
                <ResponsiveContainer width="100%" height={150}>
                  <AreaChart data={result.equity_curve} margin={{ top: 0, right: 8, bottom: 0, left: 8 }}>
                    <defs>
                      <linearGradient id="dd" x1="0" y1="0" x2="0" y2="1">
                         <stop offset="5%" stopColor="#f56565" stopOpacity={0.5} />
                         <stop offset="95%" stopColor="#f56565" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="date" hide />
                    <YAxis domain={['dataMin', 0]} hide />
                    <Tooltip
                      contentStyle={{ background: '#20203a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, color: '#fff' }}
                      formatter={(v) => [`${v}%`, 'Drawdown']}
                    />
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

        {/* Phase 5 Analytics Layout */}
        <div className="charts-layout" style={{ marginTop: '24px' }}>
           <HeatmapComponent data={result?.heatmap} />
           <MonteCarloComponent data={result?.monte_carlo} />
        </div>

        {/* Trade log */}
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Entry</th><th>Exit</th><th>SPY Entry</th>
                <th>Cost</th><th>Exit Val</th><th>P&amp;L</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {result?.trades?.length ? result.trades.map((t, i) => (
                <tr key={i}>
                  <td>{t.entry_date}</td>
                  <td>{t.exit_date}</td>
                  <td>${t.entry_spy}</td>
                  <td>${t.spread_cost}</td>
                  <td>${t.spread_exit}</td>
                  <td style={{ color: t.pnl >= 0 ? '#48bb78' : '#f56565', fontWeight: 600 }}>
                    {t.pnl >= 0 ? '+' : ''}${t.pnl}
                  </td>
                  <td>
                    {t.stopped_out
                      ? <span className="badge loss">STOP LOSS</span>
                      : t.expired
                        ? <span className="badge expired">EXPIRED</span>
                        : t.win
                          ? <span className="badge win">PROFIT</span>
                          : <span className="badge loss">LOSS</span>}
                  </td>
                </tr>
              )) : (
                <tr>
                  <td colSpan={7} style={{ textAlign: 'center', color: '#8b8b9d', padding: 32 }}>
                    {loading ? 'Running backtest…' : 'No trades found with current parameters.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ── Small helper components ── */
function Section({ label, children }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <p style={{ fontSize: '0.75rem', fontWeight: 600, color: '#6b46c1', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>
        {label}
      </p>
      {children}
    </div>
  );
}

function Field({ label, children, style }) {
  return (
    <div className="form-group" style={style}>
      <label style={{ fontSize: '0.82rem', color: '#8b8b9d', display: 'block', marginBottom: 6 }}>{label}</label>
      {children}
    </div>
  );
}

function Toggle({ name, label, checked, onChange }) {
  return (
    <div className="toggle-group">
      <label style={{ color: checked ? '#f0f0f5' : '#8b8b9d', transition: 'color .2s' }}>{label}</label>
      <label className="switch">
        <input type="checkbox" name={name} checked={checked} onChange={onChange} />
        <span className="slider" />
      </label>
    </div>
  );
}

function MetricCard({ title, value, color }) {
  return (
    <div className="metric-card">
      <span className="metric-title">{title}</span>
      <span className="metric-value" style={{ color: color || '#f0f0f5' }}>{value}</span>
    </div>
  );
}

function HeatmapComponent({ data }) {
  if (!data || data.length === 0) return null;
  
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  
  return (
    <div className="chart-box">
       <h3><Activity size={18} /> Win Rate Heatmap</h3>
       <div style={{ display: 'grid', gridTemplateColumns: `auto repeat(12, 1fr)`, gap: '4px', marginTop: '16px', overflowX: 'auto' }}>
          <div /> {/* Top-left empty cell */}
          {months.map(m => (
             <div key={m} style={{ textAlign: 'center', fontSize: '0.75rem', color: '#8b8b9d' }}>{m}</div>
          ))}
          
          {days.map(d => (
             <React.Fragment key={d}>
                <div style={{ fontSize: '0.75rem', color: '#8b8b9d', alignSelf: 'center', paddingRight: '12px' }}>{d}</div>
                {months.map(m => {
                   const cellData = data.find(x => x.day === d && x.month === m);
                   const wr = cellData ? cellData.win_rate : null;
                   const bgColor = wr === null ? '#20203a' : wr > 50 ? `rgba(72,187,120,${Math.max(0.2, (wr-50)/50)})` : `rgba(245,101,101,${Math.max(0.2, (50-wr)/50)})`;
                   
                   return (
                     <div key={m} style={{
                        background: bgColor,
                        borderRadius: '4px',
                        height: '28px',
                        width: '100%',
                        minWidth: '24px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '0.65rem',
                        color: wr === null ? '#8b8b9d' : '#fff'
                     }}>
                        {wr !== null ? `${Math.round(wr)}%` : '-'}
                     </div>
                   );
                })}
             </React.Fragment>
          ))}
       </div>
    </div>
  );
}

function MonteCarloComponent({ data }) {
   if (!data) return null;
   
   return (
     <div className="chart-box">
        <h3><BarChart2 size={18} /> Monte Carlo Permutations</h3>
        <p style={{ fontSize: '0.8rem', color: '#8b8b9d', marginBottom: '16px' }}>1,000 simulations of trade sequences randomly ordered with replacement, assuming default capital compounding.</p>
        
        <div style={{ display: 'flex', justifyContent: 'space-around', alignItems: 'center', height: '100%', padding: '20px 0' }}>
            <div style={{ textAlign: 'center' }}>
               <div style={{ fontSize: '0.85rem', color: '#f56565', textTransform: 'uppercase', marginBottom: '8px' }}>P5 (Worst Case)</div>
               <div style={{ fontSize: '1.75rem', fontWeight: 600 }}>${data.p05.toLocaleString()}</div>
            </div>
            <div style={{ textAlign: 'center', padding: '0 20px', borderLeft: '1px solid rgba(255,255,255,0.1)', borderRight: '1px solid rgba(255,255,255,0.1)' }}>
               <div style={{ fontSize: '0.85rem', color: '#a78bfa', textTransform: 'uppercase', marginBottom: '8px' }}>P50 (Median)</div>
               <div style={{ fontSize: '1.75rem', fontWeight: 600 }}>${data.p50.toLocaleString()}</div>
            </div>
            <div style={{ textAlign: 'center' }}>
               <div style={{ fontSize: '0.85rem', color: '#48bb78', textTransform: 'uppercase', marginBottom: '8px' }}>P95 (Best Case)</div>
               <div style={{ fontSize: '1.75rem', fontWeight: 600 }}>${data.p95.toLocaleString()}</div>
            </div>
        </div>
     </div>
   );
}
