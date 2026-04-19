import { useState, useEffect, useCallback, useMemo } from 'react';
import { fmtPct, fmtUsd, Card, Kpi, Badge, Btn, Switch } from '../primitives.jsx';
import { EquityChart } from '../chart.jsx';
import { api, safe } from '../api.js';
import {
  DEFAULT_CONFIG,
  BUILT_IN_PRESETS,
  loadConfig,
  saveConfig,
  loadPresets,
  savePresets,
} from '../backtestConfig.js';

const EMPTY = {
  total_return_pct: 0, sharpe: 0, max_dd_pct: 0, win_rate: 0,
  trades: 0, equity: [], total_pnl: 0, avg_pnl: 0, profit_factor: 0,
};

function mapResults(res, cfg) {
  if (!res || res.error) return EMPTY;
  const m = res.metrics || {};
  const cap = Number(cfg?.capital_allocation) || 10000;
  return {
    total_return_pct: m.total_pnl != null ? (m.total_pnl / cap) * 100 : 0,
    sharpe: m.sharpe_ratio ?? 0,
    max_dd_pct: m.max_drawdown ?? 0,
    win_rate: (m.win_rate ?? 0) / 100,
    trades: m.total_trades ?? 0,
    total_pnl: m.total_pnl ?? 0,
    avg_pnl: m.avg_pnl ?? 0,
    profit_factor: m.profit_factor ?? 0,
    equity: (res.equity_curve || []).map(d => d.equity),
  };
}

function Field({ label, children, full }) {
  return (
    <div className="field" style={full ? { gridColumn: '1 / -1' } : undefined}>
      <label>{label}</label>
      {children}
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.6, fontWeight: 700, marginBottom: 8 }}>{title}</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {children}
      </div>
    </div>
  );
}

function Toggle({ label, on, onChange }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, gridColumn: '1 / -1', padding: '2px 0' }}>
      <span style={{ color: on ? 'var(--text)' : 'var(--text-3)' }}>{label}</span>
      <Switch on={on} onChange={onChange} />
    </div>
  );
}

export function BacktestView() {
  const [config, setConfig] = useState(loadConfig);
  const [strategies, setStrategies] = useState([]);
  const [presets, setPresets] = useState(loadPresets);
  const [presetName, setPresetName] = useState('');
  const [selectedPreset, setSelectedPreset] = useState('');
  const [b, setB] = useState(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => { saveConfig(config); }, [config]);

  useEffect(() => {
    (async () => {
      const res = await safe(api.strategies, []);
      if (Array.isArray(res) && res.length) setStrategies(res);
    })();
  }, []);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.runBacktest(config);
      if (res?.error) { setError(res.error); setB(EMPTY); }
      else { setB(mapResults(res, config)); }
    } catch (e) {
      setError(e.message || 'Backtest failed');
      setB(EMPTY);
    } finally {
      setLoading(false);
    }
  }, [config]);

  useEffect(() => { run(); /* initial run on mount */ }, []); // eslint-disable-line

  const update = (key, value) => setConfig(prev => ({ ...prev, [key]: value }));
  const num = (key) => (e) => update(key, Number(e.target.value));
  const str = (key) => (e) => update(key, e.target.value);
  const bool = (key) => (v) => update(key, v);

  const allPresets = useMemo(() => ({ ...BUILT_IN_PRESETS, ...presets }), [presets]);
  const isBuiltIn = selectedPreset && selectedPreset in BUILT_IN_PRESETS;

  const applyPreset = (name) => {
    setSelectedPreset(name);
    if (!name) return;
    const p = allPresets[name];
    if (p) setConfig({ ...DEFAULT_CONFIG, ...p });
  };
  const savePreset = () => {
    const name = presetName.trim();
    if (!name) return;
    const next = { ...presets, [name]: { ...config } };
    setPresets(next); savePresets(next);
    setPresetName(''); setSelectedPreset(name);
  };
  const deletePreset = () => {
    if (!selectedPreset || isBuiltIn) return;
    const next = { ...presets };
    delete next[selectedPreset];
    setPresets(next); savePresets(next);
    setSelectedPreset('');
  };
  const resetDefaults = () => setConfig({ ...DEFAULT_CONFIG });

  return (
    <div className="page">
      <div className="grid g-5" style={{ marginBottom: 14 }}>
        <Card><Kpi label="Total Return" value={fmtPct(b.total_return_pct)} color={b.total_return_pct >= 0 ? 'var(--pos)' : 'var(--neg)'} big /></Card>
        <Card><Kpi label="Sharpe" value={b.sharpe.toFixed(2)} big /></Card>
        <Card><Kpi label="Max Drawdown" value={fmtPct(b.max_dd_pct)} color="var(--neg)" big /></Card>
        <Card><Kpi label="Win Rate" value={`${Math.round(b.win_rate * 100)}%`} big /></Card>
        <Card><Kpi label="Trades" value={b.trades} big /></Card>
      </div>

      <div className="grid g-32" style={{ marginBottom: 14 }}>
        <Card title="Equity curve" icon="trending" subtitle={b.trades ? `${b.trades} trades · ${fmtUsd(b.total_pnl, true)}` : ''} actions={
          <Btn size="sm" icon="refresh" onClick={run} disabled={loading}>
            {loading ? 'Running…' : 'Re-run'}
          </Btn>
        }>
          {error && (
            <div style={{ padding: 12, background: 'var(--neg-bg, rgba(239,68,68,0.1))', color: 'var(--neg)', fontSize: 12, borderRadius: 6, marginBottom: 10 }}>{error}</div>
          )}
          {loading && !b.equity.length && (
            <div style={{ height: 280, display: 'grid', placeItems: 'center', color: 'var(--text-3)', fontSize: 12 }}>
              Running backtest…
            </div>
          )}
          {!loading && b.equity.length > 0 && <EquityChart data={b.equity} height={280} />}
          {!loading && !b.equity.length && !error && (
            <div style={{ height: 280, display: 'grid', placeItems: 'center', color: 'var(--text-3)', fontSize: 12 }}>No data — run a backtest</div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginTop: 16, fontSize: 12 }}>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Avg P&L</div>
              <div className="mono" style={{ fontWeight: 600 }}>{fmtUsd(b.avg_pnl, true)}</div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Profit Factor</div>
              <div className="mono" style={{ fontWeight: 600 }}>{b.profit_factor.toFixed(2)}</div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Total P&L</div>
              <div className="mono" style={{ fontWeight: 600, color: b.total_pnl >= 0 ? 'var(--pos)' : 'var(--neg)' }}>{fmtUsd(b.total_pnl, true)}</div>
            </div>
          </div>
        </Card>

        <Card title="Presets" icon="sliders" subtitle={selectedPreset || 'custom'}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Field label="Apply preset">
              <select className="sel" value={selectedPreset} onChange={e => applyPreset(e.target.value)}>
                <option value="">— Select preset —</option>
                <optgroup label="Built-in">
                  {Object.keys(BUILT_IN_PRESETS).map(n => <option key={n} value={n}>{n}</option>)}
                </optgroup>
                {Object.keys(presets).length > 0 && (
                  <optgroup label="Custom">
                    {Object.keys(presets).map(n => <option key={n} value={n}>{n}</option>)}
                  </optgroup>
                )}
              </select>
            </Field>
            <Field label="Save current as">
              <div style={{ display: 'flex', gap: 6 }}>
                <input className="inp" value={presetName} onChange={e => setPresetName(e.target.value)} placeholder="My preset" style={{ flex: 1 }} />
                <Btn size="sm" variant="primary" icon="save" onClick={savePreset} disabled={!presetName.trim()}>Save</Btn>
              </div>
            </Field>
            <div style={{ display: 'flex', gap: 6 }}>
              <Btn size="sm" variant="ghost" onClick={resetDefaults} style={{ flex: 1 }}>Reset defaults</Btn>
              <Btn size="sm" variant="danger" onClick={deletePreset} disabled={!selectedPreset || isBuiltIn} style={{ flex: 1 }}>Delete</Btn>
            </div>
            <hr className="sep" />
            <Btn variant="primary" icon="play" onClick={run} disabled={loading} style={{ justifyContent: 'center', padding: 10 }}>
              {loading ? 'Running…' : 'Run Simulation'}
            </Btn>
          </div>
        </Card>
      </div>

      <div className="grid g-32" style={{ marginBottom: 14 }}>
        <Card title="Strategy & Capital" icon="cog">
          <Section title="Capital / sizing">
            <Field label="Ticker"><input className="inp" value={config.ticker} onChange={str('ticker')} /></Field>
            <Field label="History (yrs)"><input className="inp" type="number" value={config.years_history} onChange={num('years_history')} /></Field>
            <Field label="Capital ($)"><input className="inp" type="number" value={config.capital_allocation} onChange={num('capital_allocation')} /></Field>
            <Field label="Contracts"><input className="inp" type="number" value={config.contracts_per_trade} onChange={num('contracts_per_trade')} /></Field>
            <Field label="Spread cost target ($)"><input className="inp" type="number" value={config.spread_cost_target} onChange={num('spread_cost_target')} /></Field>
            <Field label="Commission / contract ($)"><input className="inp" type="number" step="0.01" value={config.commission_per_contract} onChange={num('commission_per_contract')} /></Field>
            <Toggle label="Dynamic sizing (Kelly)" on={config.use_dynamic_sizing} onChange={bool('use_dynamic_sizing')} />
            {config.use_dynamic_sizing && (
              <>
                <Field label="Risk % / trade"><input className="inp" type="number" value={config.risk_percent} onChange={num('risk_percent')} /></Field>
                <Field label="Max trade cap ($)"><input className="inp" type="number" value={config.max_trade_cap} onChange={num('max_trade_cap')} /></Field>
              </>
            )}
          </Section>

          <Section title="Strategy">
            <Field label="Logic engine" full>
              <select className="sel" value={config.strategy_id} onChange={str('strategy_id')}>
                {strategies.length === 0 && <option value="consecutive_days">Consecutive Days</option>}
                {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>
            <Field label="Topology">
              <select className="sel" value={config.topology} onChange={str('topology')}>
                <option value="vertical_spread">Vertical spread</option>
                <option value="long_call">Long call</option>
                <option value="long_put">Long put</option>
                <option value="iron_condor">Iron condor</option>
              </select>
            </Field>
            <Field label="Direction">
              <select className="sel" value={config.strategy_type} onChange={str('strategy_type')}>
                <option value="bull_call">Bull call</option>
                <option value="bear_put">Bear put</option>
              </select>
            </Field>
            <Field label="Strike width ($)"><input className="inp" type="number" value={config.strike_width} onChange={num('strike_width')} /></Field>
            <Field label="Target DTE"><input className="inp" type="number" value={config.target_dte} onChange={num('target_dte')} /></Field>
            <Field label="Entry red days"><input className="inp" type="number" value={config.entry_red_days} onChange={num('entry_red_days')} /></Field>
            <Field label="Exit green days"><input className="inp" type="number" value={config.exit_green_days} onChange={num('exit_green_days')} /></Field>
          </Section>

          <Section title="Exit / risk">
            <Field label="Stop loss %"><input className="inp" type="number" value={config.stop_loss_pct} onChange={num('stop_loss_pct')} /></Field>
            <Field label="Take profit %"><input className="inp" type="number" value={config.take_profit_pct} onChange={num('take_profit_pct')} /></Field>
            <Field label="Trailing stop %"><input className="inp" type="number" value={config.trailing_stop_pct} onChange={num('trailing_stop_pct')} /></Field>
            <Toggle label="Mark-to-market exits" on={config.use_mark_to_market} onChange={bool('use_mark_to_market')} />
          </Section>
        </Card>

        <Card title="Filters & Analytics" icon="sliders">
          <Section title="Technical filters">
            <Toggle label="RSI filter" on={config.use_rsi_filter} onChange={bool('use_rsi_filter')} />
            {config.use_rsi_filter && (
              <Field label="RSI threshold" full><input className="inp" type="number" value={config.rsi_threshold} onChange={num('rsi_threshold')} /></Field>
            )}
            <Toggle label="EMA filter (close < EMA)" on={config.use_ema_filter} onChange={bool('use_ema_filter')} />
            {config.use_ema_filter && (
              <Field label="EMA length" full><input className="inp" type="number" value={config.ema_length} onChange={num('ema_length')} /></Field>
            )}
            <Toggle label="SMA200 filter (trend up)" on={config.use_sma200_filter} onChange={bool('use_sma200_filter')} />
            <Toggle label="Volume filter" on={config.use_volume_filter} onChange={bool('use_volume_filter')} />
          </Section>

          <Section title="Macro filters">
            <Toggle label="VIX filter" on={config.use_vix_filter} onChange={bool('use_vix_filter')} />
            {config.use_vix_filter && (
              <>
                <Field label="VIX min"><input className="inp" type="number" value={config.vix_min} onChange={num('vix_min')} /></Field>
                <Field label="VIX max"><input className="inp" type="number" value={config.vix_max} onChange={num('vix_max')} /></Field>
              </>
            )}
            <Toggle label="Regime filter" on={config.use_regime_filter} onChange={bool('use_regime_filter')} />
            {config.use_regime_filter && (
              <Field label="Regime allowed" full>
                <select className="sel" value={config.regime_allowed} onChange={str('regime_allowed')}>
                  <option value="all">All</option>
                  <option value="bull">Bull only</option>
                  <option value="bear">Bear only</option>
                  <option value="neutral">Neutral only</option>
                </select>
              </Field>
            )}
          </Section>

          <Section title="Analytics">
            <Toggle label="Monte Carlo histogram" on={config.enable_mc_histogram} onChange={bool('enable_mc_histogram')} />
            <Toggle label="Walk-forward analysis" on={config.enable_walk_forward} onChange={bool('enable_walk_forward')} />
            {config.enable_walk_forward && (
              <Field label="WF windows" full><input className="inp" type="number" value={config.walk_forward_windows} onChange={num('walk_forward_windows')} /></Field>
            )}
          </Section>
        </Card>
      </div>
    </div>
  );
}
