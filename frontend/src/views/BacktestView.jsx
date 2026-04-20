import { useState, useEffect, useCallback, useMemo } from 'react';
import { fmtPct, fmtUsd, Card, Kpi, Badge, Btn, Switch } from '../primitives.jsx';
import { EquityChart, CandlestickChart } from '../chart.jsx';
import { api, safe } from '../api.js';
import { StrategyParamsForm } from '../strategyParamsForm.jsx';
import { OptimiserCard } from '../optimiserCard.jsx';
import { Ico } from '../icons.jsx';
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
  trade_list: [], mc: null, wf: [], price_history: []
};

/** Utility Components **/
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
      <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase', fontWeight: 700, marginBottom: 8, letterSpacing: 0.5 }}>{title}</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {children}
      </div>
    </div>
  );
}

function SectionBox({ title, icon, children, open = false }) {
  const [isOpen, setIsOpen] = useState(open);
  return (
    <div style={{ marginBottom: 1, border: '1px solid var(--border-soft)', borderRadius: 6, background: isOpen ? 'var(--bg-1)' : 'transparent' }}>
      <div onClick={() => setIsOpen(!isOpen)} style={{ padding: '10px 14px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--bg-2)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 11, fontWeight: 700, textTransform: 'uppercase' }}>
          <Ico name={icon} size={14} /> {title}
        </div>
        <Ico name={isOpen ? 'minus' : 'plus'} size={12} />
      </div>
      {isOpen && <div style={{ padding: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>{children}</div>}
    </div>
  );
}

function Toggle({ label, on, onChange }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, gridColumn: '1 / -1', padding: '2px 0' }}>
      <span style={{ color: on ? 'var(--text)' : 'var(--text-3)' }}>{label}</span>
      <Switch on={!!on} onChange={onChange} />
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
      try {
        const res = await api.strategies();
        if (Array.isArray(res)) setStrategies(res);
        const pRes = await safe(api.presetsList, { presets: [] });
        if (pRes?.presets) {
          const mapped = {};
          pRes.presets.forEach(p => { mapped[p.name] = p; });
          setPresets(prev => ({ ...prev, ...mapped }));
        }
      } catch (e) { console.error('Strategy fetch failed', e); }
    })();
  }, []);

  const run = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const res = await api.runBacktest(config);
      if (res?.error) { setError(res.error); setB(EMPTY); }
      else if (res) {
        const m = res.metrics || {};
        const cap = Number(config.capital_allocation) || 10000;
        setB({
          total_return_pct: m.total_pnl != null ? (m.total_pnl / cap) * 100 : 0,
          sharpe: m.sharpe_ratio ?? 0,
          max_dd_pct: m.max_drawdown ?? 0,
          win_rate: (m.win_rate ?? 0) / 100,
          trades: m.total_trades ?? 0,
          total_pnl: m.total_pnl ?? 0,
          avg_pnl: m.avg_pnl ?? 0,
          profit_factor: m.profit_factor ?? 0,
          equity: (res.equity_curve || []).map(d => d.equity),
          trade_list: res.trades || [],
          mc: res.monte_carlo,
          wf: res.walk_forward || [],
          price_history: res.price_history || [],
        });
      }
    } catch (e) { 
      setError(e.message || 'Backtest failed'); 
      setB(EMPTY); 
    } finally { setLoading(false); }
  }, [config]);

  const update = (key, value) => setConfig(prev => ({ ...prev, [key]: value }));
  const num = (key) => (e) => update(key, Number(e.target.value));
  const str = (key) => (e) => update(key, e.target.value);
  const bool = (key) => (v) => update(key, v);

  const allPresets = useMemo(() => ({ ...BUILT_IN_PRESETS, ...presets }), [presets]);
  const applyPreset = (name) => {
    setSelectedPreset(name);
    if (!name) return;
    const p = allPresets[name];
    if (p) setConfig({ ...DEFAULT_CONFIG, ...p });
  };
  const savePresetAction = async () => {
    const name = presetName.trim();
    if (!name) return;
    const next = { ...presets, [name]: config };
    setPresets(next); savePresets(next);
    setPresetName(''); setSelectedPreset(name);
  };
  const resetDefaults = () => setConfig({ ...DEFAULT_CONFIG });

  return (
    <div className="page" style={{ padding: '10px 14px', height: 'calc(100vh - 64px)', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      
      {/* 1. TOP PERFORMANCE LEDGER */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 10, marginBottom: 12 }}>
        <Card><Kpi label="Total Return" value={fmtPct(b.total_return_pct)} color={b.total_return_pct >= 0 ? 'var(--pos)' : 'var(--neg)'} big /></Card>
        <Card><Kpi label="Max Drawdown" value={fmtPct(b.max_dd_pct)} color="var(--neg)" big /></Card>
        <Card><Kpi label="Sharpe Ratio" value={(b.sharpe || 0).toFixed(2)} big /></Card>
        <Card><Kpi label="Profit Factor" value={(b.profit_factor || 0).toFixed(2)} big /></Card>
        <Card><Kpi label="Win Rate" value={Math.round((b.win_rate || 0) * 100) + '%'} big /></Card>
        <Card><Kpi label="Trade Count" value={b.trades || 0} big /></Card>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '400px 1fr 340px', gap: 14, flex: 1, minHeight: 0, overflow: 'hidden' }}>
        
        {/* 2. LEFT: CONTROL TOWER (FULL CONFIGS) */}
        <div style={{ height: '100%', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4, paddingRight: 8, minHeight: 0 }}>
          
          <SectionBox title="Asset & Timeframe" icon="cog" open>
             <Field label="Ticker"><input className="inp" value={config.ticker || ''} onChange={str('ticker')} /></Field>
             <Field label="History (Yrs)"><input className="inp" type="number" value={config.years_history || 0} onChange={num('years_history')} /></Field>
             <Field label="Initial Cap $"><input className="inp" type="number" value={config.capital_allocation || 0} onChange={num('capital_allocation')} /></Field>
             <Field label="Commission $"><input className="inp" type="number" step="0.01" value={config.commission_per_contract || 0} onChange={num('commission_per_contract')} /></Field>
          </SectionBox>

          <SectionBox title="Strategy Engine" icon="activity" open>
            <Field label="Logic" full>
              <select className="sel" value={config.strategy_id || ''} onChange={str('strategy_id')}>
                {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>
            <div style={{ gridColumn: '1/-1', marginTop: 8 }}>
              <StrategyParamsForm strategyId={config.strategy_id || 'consecutive_days'} values={config} onChange={v => setConfig(prev => ({...prev, ...v}))} />
            </div>
          </SectionBox>

          <SectionBox title="Execution Details" icon="zap">
            <Field label="Topology">
              <select className="sel" value={config.topology || ''} onChange={str('topology')}>
                <option value="vertical_spread">Vertical</option>
                <option value="long_call">Long Call</option>
                <option value="long_put">Long Put</option>
                <option value="iron_condor">Iron Condor</option>
              </select>
            </Field>
            <Field label="Direction">
              <select className="sel" value={config.strategy_type || ''} onChange={str('strategy_type')}>
                <option value="bull_call">Bull Call</option>
                <option value="bear_put">Bear Put</option>
              </select>
            </Field>
            <Field label="Strike Width"><input className="inp" type="number" value={config.strike_width || 0} onChange={num('strike_width')} /></Field>
            <Field label="Target DTE"><input className="inp" type="number" value={config.target_dte || 0} onChange={num('target_dte')} /></Field>
            <Field label="Price Target $"><input className="inp" type="number" value={config.spread_cost_target || 0} onChange={num('spread_cost_target')} /></Field>
            <Field label="Realism Factor"><input className="inp" type="number" step="0.05" value={config.realism_factor || 0} onChange={num('realism_factor')} /></Field>
          </SectionBox>

          <SectionBox title="Risk Management" icon="shield">
             <Field label="Stop Loss %"><input className="inp" type="number" value={config.stop_loss_pct || 0} onChange={num('stop_loss_pct')} /></Field>
             <Field label="Take Profit %"><input className="inp" type="number" value={config.take_profit_pct || 0} onChange={num('take_profit_pct')} /></Field>
             <Field label="Trailing %"><input className="inp" type="number" value={config.trailing_stop_pct || 0} onChange={num('trailing_stop_pct')} /></Field>
             <Toggle label="Mark-to-Market Exit" on={!!config.use_mark_to_market} onChange={bool('use_mark_to_market')} />
          </SectionBox>

          <SectionBox title="Entry Filters" icon="sliders">
             <Toggle label="RSI Filter" on={!!config.use_rsi_filter} onChange={bool('use_rsi_filter')} />
             <Toggle label="EMA Filter" on={!!config.use_ema_filter} onChange={bool('use_ema_filter')} />
             <Toggle label="VIX Filter" on={!!config.use_vix_filter} onChange={bool('use_vix_filter')} />
             <Toggle label="Regime Filter" on={!!config.use_regime_filter} onChange={bool('use_regime_filter')} />
             <Toggle label="SMA200 Trend" on={!!config.use_sma200_filter} onChange={bool('use_sma200_filter')} />
          </SectionBox>

          <Card style={{ marginTop: 6, padding: 12 }}>
             <Btn variant="primary" icon="play" onClick={run} disabled={loading} style={{ width: '100%', justifyContent: 'center', height: 44, fontSize: 13, fontWeight: 700 }}>
                {loading ? 'RE-CALCULATING...' : 'RUN SIMULATION'}
              </Btn>
              <Btn variant="ghost" size="sm" onClick={resetDefaults} style={{ width: '100%', marginTop: 8, justifyContent: 'center' }}>Reset Defaults</Btn>
          </Card>

          <Card title="Presets" icon="sliders" style={{ marginTop: 10 }}>
              <select className="sel" value={selectedPreset || ''} onChange={e => applyPreset(e.target.value)} style={{ width:'100%', marginBottom: 8 }}>
                <option value="">— Load Preset —</option>
                {Object.keys(allPresets).map(n => <option key={n} value={n}>{n}</option>)}
              </select>
          </Card>

          <OptimiserCard baseConfig={config} />
        </div>

        {/* 3. CENTER: EXECUTION ARENA */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          
          <Card title="Execution Arena" icon="trending" style={{ flex: 2, minHeight: 0 }}>
            {error && <div style={{ padding: 10, color: 'var(--neg)', background: 'rgba(239,68,68,0.1)', borderRadius: 6, marginBottom: 10, fontSize: 12 }}>{error}</div>}
            <div style={{ flex: 1, minHeight: 0 }}>
              <CandlestickChart series={b.price_history} trades={b.trade_list} height="100%" />
            </div>
          </Card>

          <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 14, flex: 1.2, minHeight: 0 }}>
             <Card title="Activity Ledger" icon="activity" flush style={{ minHeight: 0 }}>
                <div style={{ height: '100%', overflowY: 'auto' }}>
                  <table className="tbl">
                    <tbody>
                      {(b.trade_list || []).map((t, i) => (
                        <tr key={i}>
                          <td className="mono" style={{ fontSize: 10 }}>{t.entry_date}</td>
                          <td><Badge variant={t.side === 'BUY' ? 'pos' : 'neg'}>{t.side}</Badge></td>
                          <td className="num mono" style={{ color: (t.pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)', fontWeight: 600 }}>{fmtUsd(t.pnl, true)}</td>
                          <td className="num muted" style={{ fontSize: 9 }}>{t.exit_reason || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
             </Card>

             <Card title="Equity Performance" icon="activity" style={{ minHeight: 0 }}>
                <EquityChart data={b.equity} height="85%" />
                <div style={{ marginTop: 8 }}>
                   <Kpi label="Avg P&L" value={fmtUsd(b.avg_pnl, true)} color={(b.avg_pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)'} />
                </div>
             </Card>
          </div>
        </div>

        {/* 4. RIGHT: THE ANALYST */}
        <div style={{ overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
          
          <Card title="Risk Architecture" icon="radar">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
               <Section title="Monte Carlo Analysis">
                  <div style={{ gridColumn: '1/-1', display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <Kpi label="Prob. of Profit" value={b.mc ? fmtPct(b.mc.prob_profit) : '—'} color="var(--pos)" />
                    <Kpi label="VaR (95%) - Equity" value={b.mc ? fmtUsd(b.mc.p05) : '—'} color="var(--neg)" />
                    <Kpi label="Median Equity" value={b.mc ? fmtUsd(b.mc.p50) : '—'} color="var(--info)" />
                  </div>
               </Section>
               
               <Section title="Out-of-Sample Results">
                  <div style={{ gridColumn: '1/-1', fontSize: 11, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {(b.wf || []).length > 0 ? (b.wf || []).map((w, i) => (
                      <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid var(--border-soft)' }}>
                        <span className="muted">W{i+1}: {w.start_date}</span>
                        <span style={{ color: (w.pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)', fontWeight: 600 }}>{fmtUsd(w.pnl, true)}</span>
                      </div>
                    )) : <div className="muted" style={{textAlign:'center'}}>No Walk-Forward data</div>}
                  </div>
               </Section>
            </div>
          </Card>

          <Card title="Execution Stats" icon="dashboard">
             <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                  <span className="muted">Expectancy</span>
                  <span className="mono">{( ( (b.win_rate || 0) * (b.avg_pnl || 0) ) + ( (1 - (b.win_rate || 0)) * (b.avg_pnl || 0) ) ).toFixed(2)}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                  <span className="muted">Recovery Factor</span>
                  <span className="mono">{( (b.total_pnl || 0) / (Math.abs((b.max_dd_pct || 0) * 100) || 1) ).toFixed(2)}</span>
                </div>
             </div>
          </Card>

        </div>

      </div>

    </div>
  );
}
