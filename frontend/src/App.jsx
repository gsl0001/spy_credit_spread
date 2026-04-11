import React, { useState, useEffect, useRef, useCallback } from 'react';
import * as LWCharts from 'lightweight-charts';
import { AreaChart, Area, BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { Play, TrendingUp, BarChart2, Activity, Settings, Zap, Save, RotateCcw, Radio, ShieldCheck, AlertTriangle } from 'lucide-react';

const STORAGE_KEY = 'spy_backtest_config';
const PRESETS_KEY = 'spy_backtest_presets';

const DEFAULT_CONFIG = {
  ticker: "SPY", years_history: 2, capital_allocation: 10000.0,
  use_dynamic_sizing: false, risk_percent: 5.0, max_trade_cap: 0,
  contracts_per_trade: 1, spread_cost_target: 250.0, 
  strategy_id: "consecutive_days", strategy_type: "bull_call",
  topology: "vertical_spread", direction: "bull", strike_width: 5,
  take_profit_pct: 0, trailing_stop_pct: 0,
  target_dte: 14, stop_loss_pct: 50,
  commission_per_contract: 0.65, use_rsi_filter: true, rsi_threshold: 30,
  use_ema_filter: true, ema_length: 10, use_sma200_filter: false, use_volume_filter: false,
  use_mark_to_market: true, enable_mc_histogram: true,
  enable_walk_forward: false, walk_forward_windows: 4,
  use_vix_filter: false, vix_min: 15, vix_max: 35,
  use_regime_filter: false, regime_allowed: "all",
};

const BUILT_IN_PRESETS = {
  "Conservative": { ...DEFAULT_CONFIG, entry_red_days: 3, target_dte: 21, stop_loss_pct: 30, spread_cost_target: 150, use_rsi_filter: true, rsi_threshold: 25, use_regime_filter: true, regime_allowed: "bull" },
  "Aggressive": { ...DEFAULT_CONFIG, entry_red_days: 1, target_dte: 7, stop_loss_pct: 75, spread_cost_target: 400, use_rsi_filter: false, use_ema_filter: false, contracts_per_trade: 3 },
  "Post-Crash Recovery": { ...DEFAULT_CONFIG, entry_red_days: 4, target_dte: 30, stop_loss_pct: 50, use_vix_filter: true, vix_min: 25, vix_max: 60, use_rsi_filter: true, rsi_threshold: 20 },
  "Low-Vol Scalp": { ...DEFAULT_CONFIG, entry_red_days: 2, target_dte: 7, stop_loss_pct: 40, spread_cost_target: 100, use_vix_filter: true, vix_min: 10, vix_max: 20 },
  "Bear Market": { ...DEFAULT_CONFIG, strategy_type: "bear_put", entry_red_days: 2, target_dte: 14, use_regime_filter: true, regime_allowed: "bear" },
};

function loadConfig() {
  try { const s = localStorage.getItem(STORAGE_KEY); if (s) return { ...DEFAULT_CONFIG, ...JSON.parse(s) }; } catch {} return DEFAULT_CONFIG;
}
function loadPresets() {
  try { const s = localStorage.getItem(PRESETS_KEY); if (s) return JSON.parse(s); } catch {} return {};
}

export default function App() {
  const [config, setConfig] = useState(loadConfig);
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [apiError, setApiError] = useState(null);
  const [showOptimizer, setShowOptimizer] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizerResult, setOptimizerResult] = useState(null);
  const [optParamX, setOptParamX] = useState('entry_red_days');
  const [optParamY, setOptParamY] = useState('target_dte');
  const [customPresets, setCustomPresets] = useState(loadPresets);
  const [presetName, setPresetName] = useState('');
  const [appMode, setAppMode] = useState('backtest'); 
  const [ibkrHost, setIbkrHost] = useState('127.0.0.1');
  const [ibkrPort, setIbkrPort] = useState(7497);
  const [ibkrClientId, setIbkrClientId] = useState(1);
  const [ibkrAccount, setIbkrAccount] = useState(null);
  const [ibkrPositions, setIbkrPositions] = useState([]);
  const [currentTime, setCurrentTime] = useState(new Date());

  const getMarketStatus = useCallback(() => {
    const now = new Date();
    const day = now.getDay();
    const hour = now.getUTCHours();
    const min = now.getUTCMinutes();
    if (day === 0 || day === 6) return { label: 'CLOSED', color: '#f56565' };
    const floatTime = hour + min / 60;
    if (floatTime >= 13.5 && floatTime < 20) return { label: 'OPEN', color: '#48bb78' };
    return { label: 'CLOSED', color: '#f56565' };
  }, []);

  const mkt = getMarketStatus();
  
  const [paperKey, setPaperKey] = useState(localStorage.getItem('alpaca_key') || '');
  const [paperSecret, setPaperSecret] = useState(localStorage.getItem('alpaca_secret') || '');
  const [paperAccount, setPaperAccount] = useState(null);
  const [paperConnecting, setPaperConnecting] = useState(false);
  const [paperSignal, setPaperSignal] = useState(null);
  const [paperPositions, setPaperPositions] = useState([]);
  const [paperOrders, setPaperOrders] = useState([]);
  const [paperMsg, setPaperMsg] = useState('');
  const [autoScan, setAutoScan] = useState(false);
  const [autoExecute, setAutoExecute] = useState(false);
  const [scanInterval, setScanInterval] = useState(60);
  const [scanLog, setScanLog] = useState([]);
  const [isDeployed, setIsDeployed] = useState(false);
  const [deployLoading, setDeployLoading] = useState(false);
  const [ibkrOrders, setIbkrOrders] = useState([]);
  const [lastHeartbeat, setLastHeartbeat] = useState(null);
  const [connStatus, setConnStatus] = useState('offline');
  const autoScanRef = useRef(null);

  const calculateTotalPnl = useCallback(() => {
    const positions = appMode === 'live' ? ibkrPositions : appMode === 'paper' ? paperPositions : [];
    return positions.reduce((sum, p) => sum + (p.unrealized_pl || 0), 0);
  }, [appMode, ibkrPositions, paperPositions]);

  const totalUnrealizedPnl = calculateTotalPnl();

  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const paperKeyRef = useRef(paperKey);
  const paperSecretRef = useRef(paperSecret);

  useEffect(() => {
    if (!chartContainerRef.current) return;
    const chart = LWCharts.createChart(chartContainerRef.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#8b8b9d' },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.1)', timeVisible: true },
    });
    chartRef.current = chart;
    const series = chart.addSeries(LWCharts.CandlestickSeries, { upColor: '#48bb78', downColor: '#f56565', borderVisible: false, wickUpColor: '#48bb78', wickDownColor: '#f56565' });
    seriesRef.current = series;
    return () => { if(chartRef.current) { chartRef.current.remove(); chartRef.current = null; seriesRef.current = null; } };
  }, []);

  useEffect(() => {
    if (!result || !seriesRef.current || !chartRef.current) return;
    const prices = (result.price_history || []).filter(p => p.open != null && p.high != null && p.low != null && p.close != null);
    seriesRef.current.setData(prices);
    const markers = [];
    (result.trades || []).forEach(t => {
      if (t.entry_date) markers.push({ time: t.entry_date, position: 'belowBar', color: '#48bb78', shape: 'arrowUp', text: config.strategy_type === 'bear_put' ? 'PUT' : 'BUY' });
      if (t.exit_date) markers.push({ time: t.exit_date, position: 'aboveBar', color: t.win ? '#48bb78' : '#f56565', shape: 'arrowDown', text: t.stopped_out ? 'STOP' : t.expired ? 'EXP' : t.win ? 'WIN' : 'LOSS' });
    });
    markers.sort((a, b) => a.time < b.time ? -1 : a.time > b.time ? 1 : 0);
    seriesRef.current.setMarkers(markers);
    chartRef.current.timeScale().fitContent();
  }, [result, config.strategy_type]);

  const runSimulation = useCallback(async () => {
    setLoading(true); setApiError(null);
    try {
      const res = await fetch('http://127.0.0.1:8000/api/backtest', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) });
      const json = await res.json(); if (json.error) throw new Error(json.error);
      setResult(json);
    } catch (e) { setApiError(e.message); } finally { setLoading(false); }
  }, [config]);

  const loadIbkrData = useCallback(async () => {
    try {
      const creds = { host: ibkrHost, port: ibkrPort, client_id: ibkrClientId };
      const [accRes, posRes] = await Promise.all([
        fetch('http://127.0.0.1:8000/api/ibkr/connect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(creds) }),
        fetch('http://127.0.0.1:8000/api/ibkr/positions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(creds) })
      ]);
      const accJson = await accRes.json();
      if (accJson.connected) setIbkrAccount(accJson.summary);
      const posJson = await posRes.json();
      setIbkrPositions(posJson.positions || []);
    } catch (e) { console.error("Poll failed:", e); }
  }, [ibkrHost, ibkrPort, ibkrClientId]);

  const checkIbkrOrders = useCallback(async () => {
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/ibkr/orders?host=${ibkrHost}&port=${ibkrPort}&client_id=${ibkrClientId}`);
      const json = await res.json();
      if (json.orders) setIbkrOrders(json.orders);
    } catch (e) { console.error("Could not fetch orders:", e); }
  }, [ibkrHost, ibkrPort, ibkrClientId]);

  const loadPaperData = useCallback(async () => {
    try {
      const [posRes, ordRes] = await Promise.all([
        fetch('http://127.0.0.1:8000/api/paper/positions', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({api_key:paperKey, api_secret:paperSecret}) }),
        fetch('http://127.0.0.1:8000/api/paper/orders', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({api_key:paperKey, api_secret:paperSecret}) }),
      ]);
      const posJson = await posRes.json(); setPaperPositions(posJson.positions || []);
      const ordJson = await ordRes.json(); setPaperOrders(ordJson.orders || []);
    } catch(e) { console.error(e); }
  }, [paperKey, paperSecret]);

  const scanSignal = useCallback(async (isAuto = false) => {
    try {
      const key = paperKeyRef.current;
      const secret = paperSecretRef.current;
      const res = await fetch('http://127.0.0.1:8000/api/paper/scan', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({api_key:key, api_secret:secret, config}) });
      const data = await res.json();
      setPaperSignal(data);
      const now = new Date().toLocaleTimeString();
      setScanLog(prev => [{ time: now, signal: data.signal, price: data.price, rsi: data.rsi }, ...prev].slice(0, 20));
      if (isAuto && data.signal && autoExecute) {
        const side = config.strategy_type === 'bear_put' ? 'sell' : 'buy';
        const execRes = await fetch('http://127.0.0.1:8000/api/paper/execute', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({api_key:key, api_secret:secret, symbol:config.ticker, qty:config.contracts_per_trade * 100, side}) });
        const execJson = await execRes.json();
        if (execJson.success) {
          setPaperMsg(`🤖 AUTO-EXECUTED: ${execJson.side} ${execJson.qty} ${execJson.symbol}`);
          loadPaperData();
        }
      }
    } catch(e) { console.error(e); }
  }, [config, autoExecute, loadPaperData]);

  useEffect(() => {
    const fetchStrats = async () => {
      try {
        const res = await fetch('http://127.0.0.1:8000/api/strategies');
        const data = await res.json();
        setStrategies(data);
      } catch (e) { console.error("Could not fetch strategies:", e); }
    };
    fetchStrats();
    runSimulation();
  }, [runSimulation]);

  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (appMode === 'backtest') return;
    const poll = async () => {
      if (appMode === 'live') {
        if (ibkrAccount) await loadIbkrData();
        await checkIbkrOrders();
      }
      if (appMode === 'paper' && paperAccount) await loadPaperData();
    };
    const interval = setInterval(poll, 30000); 
    return () => clearInterval(interval);
  }, [appMode, ibkrAccount, paperAccount, loadIbkrData, checkIbkrOrders, loadPaperData]);

  useEffect(() => {
    if (appMode !== 'live') {
      setConnStatus('offline');
      return;
    }
    const hb = async () => {
      try {
        const res = await fetch(`http://127.0.0.1:8000/api/ibkr/connect`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ host: ibkrHost, port: ibkrPort, client_id: ibkrClientId })
        });
        const json = await res.json();
        if (json.connected) {
          setConnStatus('online');
          setLastHeartbeat(new Date());
        } else if (connStatus === 'online') {
          setConnStatus('dropped');
        }
      } catch (e) {
        if (connStatus === 'online') setConnStatus('dropped');
      }
    };
    const interval = setInterval(hb, 10000);
    hb();
    return () => clearInterval(interval);
  }, [appMode, ibkrHost, ibkrPort, ibkrClientId, connStatus]);

  useEffect(() => {
    if (autoScan && paperAccount) {
      scanSignal(true);
      autoScanRef.current = setInterval(() => scanSignal(true), scanInterval * 1000);
      return () => clearInterval(autoScanRef.current);
    }
  }, [autoScan, scanInterval, paperAccount, scanSignal]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setConfig(prev => {
      const next = { ...prev, [name]: type === 'checkbox' ? checked : isNaN(Number(value)) ? value : value === '' ? value : Number(value) };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); return next;
    });
  };

  const m = result?.metrics ?? {};
  const allPresets = { ...BUILT_IN_PRESETS, ...customPresets };

  return (
    <div className="app-container">
      <div className="sidebar">
        <div className="sidebar-header">
          <h1><Activity size={22} /> Neural Engine</h1>
          <p style={{ fontSize: '0.62rem', color: '#8b8b9d', marginTop: -4, fontWeight: 700, textTransform: 'uppercase' }}>SPY Options Backtester v2.0</p>
        </div>

        <Section label="📡 Trading Engine">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginBottom: 12 }}>
            {['backtest', 'paper', 'live'].map(mode => (
              <button key={mode} onClick={() => setAppMode(mode)} style={{ 
                padding: '10px 5px', borderRadius: 8, border: `1px solid ${appMode === mode ? 'var(--accent)' : 'var(--border)'}`, 
                background: appMode === mode ? 'rgba(107,70,193,0.2)' : 'var(--bg-card)', 
                color: appMode === mode ? '#a78bfa' : '#8b8b9d', fontSize: '0.7rem', fontWeight: 700, cursor: 'pointer', textTransform: 'uppercase' 
              }}>
                {mode}
              </button>
            ))}
          </div>
        </Section>
        
        <Section label="Strategy Config">
          <Field label="Logic Engine">
            <select name="strategy_id" value={config.strategy_id} onChange={handleChange} style={{ width: '100%', background: 'var(--bg-card)', color: '#fff', border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
              {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          <div style={{ marginTop: 20 }}>
            <button className="btn-primary" onClick={runSimulation} disabled={loading} style={{ width: '100%', padding: 12 }}>
              {loading ? 'Running...' : 'Run Simulation'}
            </button>
          </div>
        </Section>
      </div>

      <div className="main-content">
        <header className="dashboard-header">
          <div style={{ display: 'flex', gap: 32 }}>
            <div className="hud-item">
              <span className="hud-label">System Time</span>
              <span className="hud-value">{currentTime.toLocaleTimeString([], { hour12: false })}</span>
            </div>
            <div className="hud-item">
              <span className="hud-label">TWS Status</span>
              <span className="hud-value" style={{ color: connStatus === 'online' ? 'var(--success)' : 'var(--danger)' }}>{connStatus.toUpperCase()}</span>
            </div>
          </div>
        </header>

        <div className="content-body">
          {apiError && <div className="error-banner">⚠ {apiError}</div>}
          <div className="metrics-grid">
            <MetricCard title="Total P&L" value={`$${(m.total_pnl ?? 0).toLocaleString()}`} color={(m.total_pnl ?? 0) >= 0 ? '#48bb78' : '#f56565'} />
            <MetricCard title="Win Rate" value={`${m.win_rate ?? 0}%`} />
            <MetricCard title="Trades" value={m.total_trades ?? 0} />
          </div>
          <div className="charts-layout">
            <div className="chart-box">
              <h3><BarChart2 size={18} /> Price Action</h3>
              <div ref={chartContainerRef} style={{ width: '100%', height: '420px' }} />
            </div>
            <div className="chart-box">
              <h3><TrendingUp size={18} /> Equity</h3>
              {result?.equity_curve ? (
                <ResponsiveContainer width="100%" height={260}>
                  <AreaChart data={result.equity_curve}>
                    <XAxis dataKey="date" hide /><YAxis hide />
                    <Tooltip />
                    <Area type="monotone" dataKey="equity" stroke="#a78bfa" fill="#6b46c1" fillOpacity={0.1} />
                  </AreaChart>
                </ResponsiveContainer>
              ) : <div style={{ height: 260, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#8b8b9d' }}>No data</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Section({label,children}){ return <div style={{marginBottom:18}}><p style={{fontSize:'0.72rem',fontWeight:600,color:'#6b46c1',textTransform:'uppercase',letterSpacing:1,marginBottom:8}}>{label}</p>{children}</div>; }
function Field({label,children,style}){ return <div className="form-group" style={style}><label style={{fontSize:'0.78rem',color:'#8b8b9d',display:'block',marginBottom:5}}>{label}</label>{children}</div>; }
function Toggle({name,label,checked,onChange}){ return <div className="toggle-group"><label style={{color:checked?'#f0f0f5':'#8b8b9d',transition:'color .2s'}}>{label}</label><label className="switch"><input type="checkbox" name={name} checked={checked} onChange={onChange}/><span className="slider"/></label></div>; }
function MetricCard({title,value,color}){ 
  return (
    <div className="metric-card">
      <span className="metric-label">{title}</span>
      <span className="metric-value" style={{color:color||'#f0f0f5'}}>{value}</span>
    </div>
  ); 
}
