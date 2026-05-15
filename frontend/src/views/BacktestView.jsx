import { useState, useEffect, useCallback, useMemo } from 'react';
import { fmtPct, fmtUsd, Kpi, Badge, Btn, Switch } from '../primitives.jsx';
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
  recovery_factor: 0, avg_hold_days: 0, max_consec_losses: 0,
  trade_list: [], mc: null, wf: [], price_history: [],
};

function presetToBacktestConfig(preset) {
  if (!preset) return { ...DEFAULT_CONFIG };
  const entryFilters = preset.entry_filters || {};
  const strategyParams = preset.strategy_params || {};
  const sizingParams = preset.sizing_params || {};
  const fixedContracts = sizingParams.fixed_contracts;
  const capitalAllocation = sizingParams.capital_allocation;
  const maxAllocationCap = sizingParams.max_allocation_cap;
  return {
    ...DEFAULT_CONFIG,
    ...preset,
    ...entryFilters,
    ...strategyParams,
    strategy_id: preset.strategy_id || preset.strategy_name || DEFAULT_CONFIG.strategy_id,
    contracts_per_trade: Number(fixedContracts ?? preset.contracts_per_trade ?? DEFAULT_CONFIG.contracts_per_trade),
    capital_allocation: Number(capitalAllocation ?? preset.capital_allocation ?? DEFAULT_CONFIG.capital_allocation),
    max_trade_cap: Number(maxAllocationCap ?? preset.max_trade_cap ?? DEFAULT_CONFIG.max_trade_cap),
    risk_percent: Number(sizingParams.risk_percent ?? preset.risk_percent ?? DEFAULT_CONFIG.risk_percent),
    strategy_params: strategyParams,
    entry_filters: entryFilters,
    sizing_params: sizingParams,
  };
}

/* ── small primitives ─────────────────────────────────────── */

function Field({ label, children, full }) {
  return (
    <div className="field" style={full ? { gridColumn: '1 / -1' } : undefined}>
      <label>{label}</label>
      {children}
    </div>
  );
}

function Toggle({ label, on, onChange }) {
  return (
    <div className="toggle-row">
      <span style={{ color: on ? 'var(--text)' : 'var(--text-3)' }}>{label}</span>
      <Switch on={!!on} onChange={onChange} />
    </div>
  );
}

function CardHead({ icon, title, actions }) {
  return (
    <div className="card__head">
      <span className="title"><Ico name={icon} size={12} /> {title}</span>
      {actions && <div className="card__actions">{actions}</div>}
    </div>
  );
}

function StatRow({ label, value, color }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, padding: '6px 0', borderBottom: '1px solid var(--border-soft)' }}>
      <span style={{ color: 'var(--text-3)' }}>{label}</span>
      <span className="mono" style={color ? { color } : undefined}>{value}</span>
    </div>
  );
}

/* ── main view ───────────────────────────────────────────── */

export function BacktestView() {
  const [config, setConfig] = useState(loadConfig);
  const [strategies, setStrategies] = useState([]);
  const [presets, setPresets] = useState(loadPresets);
  const [presetName, setPresetName] = useState('');
  const [selectedPreset, setSelectedPreset] = useState('');
  const [b, setB] = useState(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedTrade, setSelectedTrade] = useState(null);
  const [activeConfigSection, setActiveConfigSection] = useState('market');

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
    setLoading(true); setError(null); setSelectedTrade(null);
    try {
      const res = await api.runBacktest(config);
      if (res?.error) { setError(res.error); setB(EMPTY); }
      else if (res) {
        const m = res.metrics || {};
        const cap = Number(config.capital_allocation) || 10000;
        setB({
          total_return_pct: m.total_pnl != null && cap > 0 ? (m.total_pnl / cap) * 100 : 0,
          sharpe: m.sharpe_ratio ?? 0,
          max_dd_pct: m.max_drawdown ?? 0,
          win_rate: (m.win_rate ?? 0) / 100,
          trades: m.total_trades ?? 0,
          total_pnl: m.total_pnl ?? 0,
          avg_pnl: m.avg_pnl ?? 0,
          profit_factor: m.profit_factor ?? 0,
          recovery_factor: m.recovery_factor ?? 0,
          avg_hold_days: m.avg_hold_days ?? 0,
          max_consec_losses: m.max_consec_losses ?? 0,
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

  const update = (key, val) => setConfig(prev => ({ ...prev, [key]: val }));
  const num = key => e => update(key, Number(e.target.value));
  const str = key => e => update(key, e.target.value);
  const bool = key => v => update(key, v);

  const allPresets = useMemo(() => ({ ...BUILT_IN_PRESETS, ...presets }), [presets]);

  const applyPreset = name => {
    setSelectedPreset(name);
    if (name && allPresets[name]) setConfig(presetToBacktestConfig(allPresets[name]));
  };

  const savePreset = () => {
    const name = presetName.trim();
    if (!name) return;
    const next = { ...presets, [name]: config };
    setPresets(next); savePresets(next);
    setPresetName(''); setSelectedPreset(name);
  };

  const deletePreset = name => {
    if (!presets[name]) return;
    const next = { ...presets };
    delete next[name];
    setPresets(next); savePresets(next);
    if (selectedPreset === name) setSelectedPreset('');
  };

  // Has the user run a sim yet? Determines whether to show empty-state placeholders.
  const hasRun = (b.trades || 0) > 0 || (b.equity || []).length > 0 || (b.price_history || []).length > 0;
  const selectedStrategy = strategies.find(s => s.id === config.strategy_id);
  const strategyLabel = selectedStrategy?.name || config.strategy_id || 'Strategy';
  const topologyLabel = String(config.topology || 'vertical_spread').replace(/_/g, ' ');
  const barSize = selectedStrategy?.bar_size || '1 day';
  const isIntraday = barSize !== '1 day';
  // yfinance hard caps for intraday history (in days). Anything else → years.
  const intradayHistoryDays = {
    '1 min': 7,
    '5 mins': 60,
    '15 mins': 60,
    '30 mins': 60,
    '1 hour': 730,
  };
  const tfLabel = barSize === '1 day' ? 'Daily' : barSize.replace(' mins', 'm').replace(' min', 'm').replace(' hour', 'h').replace(' day', 'd');

  // Auto-adjust years_history when switching to intraday — yfinance won't
  // serve more than the cap above. Convert capped days to fractional years
  // so the request payload is valid.
  useEffect(() => {
    if (!isIntraday) return;
    const capDays = intradayHistoryDays[barSize] || 60;
    const capYears = Math.max(1, Math.round((capDays / 365) * 10) / 10);
    if ((config.years_history || 0) > capYears + 0.05) {
      setConfig(prev => ({ ...prev, years_history: capYears }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.strategy_id, barSize]);
  const configSections = [
    { id: 'market', title: 'Market', icon: 'cog', hint: 'Ticker, history window, capital, and commission.' },
    { id: 'strategy', title: 'Strategy', icon: 'activity', hint: 'Signal logic and strategy-specific parameters.' },
    { id: 'sizing', title: 'Sizing', icon: 'target', hint: 'Fixed contracts or percent-risk sizing controls.' },
    { id: 'structure', title: 'Structure', icon: 'zap', hint: 'Option topology, DTE, strike width, and fill realism.' },
    { id: 'risk', title: 'Risk', icon: 'shield', hint: 'Exit rules used during simulation.' },
    { id: 'filters', title: 'Filters', icon: 'sliders', hint: 'Optional market filters before entry.' },
    { id: 'analysis', title: 'Analysis', icon: 'radar', hint: 'Monte Carlo and walk-forward outputs.' },
    { id: 'optimizer', title: 'Optimizer', icon: 'dashboard', hint: 'Parameter sweep for the current base setup.' },
  ];

  const activeSectionMeta = configSections.find(s => s.id === activeConfigSection) || configSections[0];
  const renderConfigSection = () => {
    switch (activeConfigSection) {
      case 'strategy':
        return (
          <>
            <Field label="Logic" full>
              <select className="sel" value={config.strategy_id || ''} onChange={str('strategy_id')}>
                {strategies.length === 0 && <option value="">— loading… —</option>}
                {strategies.map(s => {
                  const v = s.vetting_result || 'pending';
                  const verdictTag = v === 'rejected' ? ' ⊘ rejected'
                    : v === 'shipped' ? ''
                    : ' · pending';
                  const tfTag = s.bar_size && s.bar_size !== '1 day' ? ` · ${s.bar_size}` : '';
                  return (
                    <option key={s.id} value={s.id}>{s.name}{tfTag}{verdictTag}</option>
                  );
                })}
              </select>
            </Field>
            <div style={{ gridColumn: '1/-1' }}>
              <StrategyParamsForm
                strategyId={config.strategy_id || 'consecutive_days'}
                values={config}
                onChange={v => setConfig(prev => ({ ...prev, ...v }))}
              />
            </div>
          </>
        );
      case 'sizing':
        return (
          <>
            <Toggle label="Dynamic Sizing" on={!!config.use_dynamic_sizing} onChange={bool('use_dynamic_sizing')} />
            {config.use_dynamic_sizing ? (
              <>
                <Field label="Risk %">
                  <input className="inp" type="number" step="0.5" value={config.risk_percent || 0} onChange={num('risk_percent')} />
                </Field>
                <Field label="Max Trade Cap $">
                  <input className="inp" type="number" value={config.max_trade_cap || 0} onChange={num('max_trade_cap')} />
                </Field>
              </>
            ) : (
              <Field label="Contracts / Trade">
                <input className="inp" type="number" min="1" value={config.contracts_per_trade || 1} onChange={num('contracts_per_trade')} />
              </Field>
            )}
          </>
        );
      case 'structure':
        return (
          <>
            <Field label="Topology">
              <select className="sel" value={config.topology || ''} onChange={str('topology')}>
                <option value="vertical_spread">Vertical Spread</option>
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
            <Field label="Strike Width">
              <input className="inp" type="number" value={config.strike_width || 0} onChange={num('strike_width')} />
            </Field>
            <Field label="Target DTE">
              <input className="inp" type="number" value={config.target_dte || 0} onChange={num('target_dte')} />
            </Field>
            <Field label="Spread Target $">
              <input className="inp" type="number" value={config.spread_cost_target || 0} onChange={num('spread_cost_target')} />
            </Field>
            <Field label="Realism Factor">
              <input className="inp" type="number" step="0.05" value={config.realism_factor || 0} onChange={num('realism_factor')} />
            </Field>
          </>
        );
      case 'risk':
        return (
          <>
            <Field label="Stop Loss %">
              <input className="inp" type="number" value={config.stop_loss_pct || 0} onChange={num('stop_loss_pct')} />
            </Field>
            <Field label="Take Profit %">
              <input className="inp" type="number" value={config.take_profit_pct || 0} onChange={num('take_profit_pct')} />
            </Field>
            <Field label="Trailing Stop %">
              <input className="inp" type="number" value={config.trailing_stop_pct || 0} onChange={num('trailing_stop_pct')} />
            </Field>
            <Toggle label="Mark-to-Market Exit" on={!!config.use_mark_to_market} onChange={bool('use_mark_to_market')} />
          </>
        );
      case 'filters':
        return (
          <>
            <Toggle label="RSI Filter" on={!!config.use_rsi_filter} onChange={bool('use_rsi_filter')} />
            {config.use_rsi_filter && (
              <Field label="RSI Threshold">
                <input className="inp" type="number" value={config.rsi_threshold ?? 30} onChange={num('rsi_threshold')} />
              </Field>
            )}
            <Toggle label="EMA Filter" on={!!config.use_ema_filter} onChange={bool('use_ema_filter')} />
            {config.use_ema_filter && (
              <Field label="EMA Length">
                <input className="inp" type="number" value={config.ema_length ?? 10} onChange={num('ema_length')} />
              </Field>
            )}
            <Toggle label="VIX Filter" on={!!config.use_vix_filter} onChange={bool('use_vix_filter')} />
            {config.use_vix_filter && (
              <>
                <Field label="VIX Min">
                  <input className="inp" type="number" value={config.vix_min ?? 15} onChange={num('vix_min')} />
                </Field>
                <Field label="VIX Max">
                  <input className="inp" type="number" value={config.vix_max ?? 35} onChange={num('vix_max')} />
                </Field>
              </>
            )}
            <Toggle label="Regime Filter" on={!!config.use_regime_filter} onChange={bool('use_regime_filter')} />
            {config.use_regime_filter && (
              <Field label="Regime" full>
                <select className="sel" value={config.regime_allowed || 'all'} onChange={str('regime_allowed')}>
                  <option value="all">All Regimes</option>
                  <option value="bull">Bull Only</option>
                  <option value="bear">Bear Only</option>
                  <option value="neutral">Neutral Only</option>
                </select>
              </Field>
            )}
            <Toggle label="SMA 200 Trend" on={!!config.use_sma200_filter} onChange={bool('use_sma200_filter')} />
            <Toggle label="Volume Filter" on={!!config.use_volume_filter} onChange={bool('use_volume_filter')} />
          </>
        );
      case 'analysis':
        return (
          <>
            <Toggle label="Monte Carlo" on={!!config.enable_mc_histogram} onChange={bool('enable_mc_histogram')} />
            <Toggle label="Walk-Forward" on={!!config.enable_walk_forward} onChange={bool('enable_walk_forward')} />
            {config.enable_walk_forward && (
              <Field label="WF Windows" full>
                <input className="inp" type="number" min="2" max="12" value={config.walk_forward_windows ?? 4} onChange={num('walk_forward_windows')} />
              </Field>
            )}
          </>
        );
      case 'optimizer':
        return <OptimiserCard baseConfig={config} />;
      case 'market':
      default:
        return (
          <>
            <Field label="Ticker">
              <input className="inp" value={config.ticker || ''} onChange={str('ticker')} />
            </Field>
            <Field label={isIntraday ? `History (yrs · capped by yfinance to ${intradayHistoryDays[barSize] || 60}d)` : 'History (yrs)'}>
              <input
                className="inp"
                type="number"
                step={isIntraday ? '0.1' : '1'}
                max={isIntraday ? (Math.round((intradayHistoryDays[barSize] || 60) / 365 * 10) / 10) : undefined}
                value={config.years_history || 0}
                onChange={num('years_history')}
              />
            </Field>
            <Field label="Bar Size">
              <input className="inp" value={barSize} disabled readOnly title="Set by the strategy class (BAR_SIZE)" />
            </Field>
            <Field label="Initial Capital $">
              <input className="inp" type="number" value={config.capital_allocation || 0} onChange={num('capital_allocation')} />
            </Field>
            <Field label="Commission $">
              <input className="inp" type="number" step="0.01" value={config.commission_per_contract || 0} onChange={num('commission_per_contract')} />
            </Field>
          </>
        );
    }
  };

  /* ─────────────────────── RENDER ─────────────────────── */
  return (
    <div className="workspace-split">

      {/* ═══════════ LEFT SIDEBAR — scrollable config ═══════════ */}
      <div className="workspace-sidebar config-panel">
        <div className="config-panel-header">
          <div>
            <div className="config-eyebrow">Backtest Setup</div>
            <div className="config-title">{config.ticker || 'SPY'} strategy lab</div>
          </div>
          <div className="config-chip-grid">
            <div className="config-chip">
              <span>Strategy</span>
              <strong>{strategyLabel}</strong>
            </div>
            <div className="config-chip">
              <span>Capital</span>
              <strong>{fmtUsd(config.capital_allocation || 0)}</strong>
            </div>
            <div className="config-chip">
              <span>Structure</span>
              <strong>{topologyLabel}</strong>
            </div>
            <div className="config-chip">
              <span>DTE</span>
              <strong>{config.target_dte ?? 0}</strong>
            </div>
            <div className="config-chip" title={`Bar size: ${barSize}`}>
              <span>Timeframe</span>
              <strong>{tfLabel}{isIntraday ? ' · 0DTE-capable' : ''}</strong>
            </div>
          </div>
          <div className="config-actions">
            <button
              className="btn primary"
              onClick={run}
              disabled={loading}
            >
              <Ico name="zap" size={13} /> {loading ? 'Calculating' : 'Run Simulation'}
            </button>
            <Btn size="sm" variant="ghost" onClick={() => { setConfig({ ...DEFAULT_CONFIG }); setSelectedPreset(''); setError(null); }}>
              Reset
            </Btn>
          </div>
          <div className="config-preset-tools">
            <div className="config-preset-head">
              <span><Ico name="book" size={12} /> Preset</span>
              {selectedPreset && presets[selectedPreset] && (
                <button className="config-link danger" onClick={() => deletePreset(selectedPreset)}>
                  Delete
                </button>
              )}
            </div>
            <select className="sel" value={selectedPreset || ''} onChange={e => applyPreset(e.target.value)}>
              <option value="">Load preset</option>
              {Object.keys(allPresets).map(n => <option key={n} value={n}>{n}</option>)}
            </select>
            <div className="preset-save-row">
              <input
                className="inp"
                placeholder="Save current setup as"
                value={presetName}
                onChange={e => setPresetName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && savePreset()}
              />
              <Btn size="sm" variant="primary" onClick={savePreset} disabled={!presetName.trim()}>Save</Btn>
            </div>
          </div>
        </div>

        <div className="config-workbench">
          <div className="config-tabs" aria-label="Backtest configuration sections">
            {configSections.map(section => (
              <button
                key={section.id}
                type="button"
                className="config-tab"
                aria-current={activeConfigSection === section.id ? 'true' : undefined}
                onClick={() => setActiveConfigSection(section.id)}
                title={section.hint}
              >
                <Ico name={section.icon} size={13} />
                <span>{section.title}</span>
              </button>
            ))}
          </div>

          <div className="config-editor">
            <div className="config-editor__head">
              <div>
                <div className="config-editor__title">
                  <Ico name={activeSectionMeta.icon} size={14} /> {activeSectionMeta.title}
                </div>
                <div className="config-editor__hint">{activeSectionMeta.hint}</div>
              </div>
            </div>
            <div className={activeConfigSection === 'optimizer' ? 'config-editor__body config-editor__body--single' : 'config-editor__body'}>
              {renderConfigSection()}
            </div>
          </div>
        </div>
      </div>

      {/* ═══════════ RIGHT MAIN — scrollable results ═══════════ */}
      <div className="workspace-main">

        {/* KPI strip */}
        <div className="kpi-strip">
          {[
            { label: 'Total Return', value: fmtPct(b.total_return_pct), color: b.total_return_pct >= 0 ? 'var(--pos)' : 'var(--neg)' },
            { label: 'Max Drawdown', value: fmtPct(b.max_dd_pct), color: 'var(--neg)' },
            { label: 'Sharpe Ratio', value: (b.sharpe || 0).toFixed(2) },
            { label: 'Profit Factor', value: (b.profit_factor || 0).toFixed(2) },
            { label: 'Win Rate', value: Math.round((b.win_rate || 0) * 100) + '%' },
            { label: 'Trade Count', value: b.trades || 0 },
          ].map(({ label, value, color }) => (
            <div key={label} className="card">
              <div className="card__body"><Kpi label={label} value={value} color={color} big /></div>
            </div>
          ))}
        </div>

        {/* Chart + Execution Stats side by side */}
        <div className="bt-chart-grid">
          {/* Chart */}
          <div className="card" style={{ display: 'flex', flexDirection: 'column' }}>
            <CardHead
              icon="trending"
              title={`Price & Trades${b.trades ? ` · ${b.trades} trades` : ''}`}
              actions={selectedTrade !== null && (
                <button onClick={() => setSelectedTrade(null)}
                  style={{ background: 'none', border: 0, color: 'var(--text-3)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, fontSize: 11 }}>
                  <Ico name="x" size={11} /> Clear
                </button>
              )}
            />
            {error && (
              <div style={{ padding: '6px 12px', color: 'var(--neg)', background: 'oklch(38% .18 20 / 0.15)', borderBottom: '1px solid var(--border-soft)', fontSize: 12 }}>
                {error}
              </div>
            )}
            <div className="chart-frame">
              {hasRun ? (
                <CandlestickChart
                  series={b.price_history}
                  trades={b.trade_list}
                  height={520}
                  selectedTrade={selectedTrade}
                  onTradeSelect={setSelectedTrade}
                />
              ) : (
                <div className="empty-state">
                  <Ico name="trending" size={28} />
                  <div>No simulation yet. Configure inputs on the left, then run the model.</div>
                </div>
              )}
              {loading && (
                <div style={{
                  position: 'absolute', inset: 0,
                  background: 'rgba(0,0,0,.45)', backdropFilter: 'blur(2px)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 12, color: 'var(--text)', fontWeight: 600, gap: 10,
                }}>
                  <Ico name="zap" size={16} /> Running backtest…
                </div>
              )}
            </div>
          </div>

          {/* Execution Stats */}
          <div className="card" style={{ display: 'flex', flexDirection: 'column' }}>
            <CardHead icon="dashboard" title="Execution Stats" />
            <div className="card__body" style={{ padding: '4px var(--pad-x)', flex: 1 }}>
              <StatRow label="Total P&L" value={fmtUsd(b.total_pnl, true)} color={(b.total_pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)'} />
              <StatRow label="Avg P&L / trade" value={fmtUsd(b.avg_pnl, true)} color={(b.avg_pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)'} />
              <StatRow label="Win Rate" value={Math.round((b.win_rate || 0) * 100) + '%'} />
              <StatRow label="Profit Factor" value={(b.profit_factor || 0).toFixed(2)} />
              <StatRow label="Sharpe Ratio" value={(b.sharpe || 0).toFixed(2)} />
              <StatRow
                label="Expectancy"
                value={((b.win_rate || 0) * (b.avg_pnl || 0)).toFixed(2)}
                color={((b.win_rate || 0) * (b.avg_pnl || 0)) >= 0 ? 'var(--pos)' : 'var(--neg)'}
              />
              <StatRow
                label="Recovery Factor"
                value={(b.recovery_factor || 0).toFixed(2)}
              />
              <StatRow label="Avg Hold (days)" value={(b.avg_hold_days || 0).toFixed(1)} />
              <StatRow
                label="Max Consec Losses"
                value={b.max_consec_losses || 0}
                color={b.max_consec_losses > 5 ? 'var(--neg)' : undefined}
              />
            </div>
          </div>
        </div>

        {/* Bottom row: Trade Log + Equity/MC/WF */}
        <div className="bt-bottom-grid">

          {/* Trade Log */}
          <div className="card" style={{ display: 'flex', flexDirection: 'column' }}>
            <CardHead icon="activity" title="Trade Log" />
            <div style={{ maxHeight: 340, overflowY: 'auto' }}>
              {b.trade_list.length === 0 ? (
                <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>
                  No trades yet — run a simulation
                </div>
              ) : (
                <table className="tbl">
                  <thead style={{ position: 'sticky', top: 0, zIndex: 5, background: 'var(--bg-1)' }}>
                    <tr>
                      <th>Entry</th>
                      <th>Exit</th>
                      <th>Side</th>
                      <th className="num">Days</th>
                      <th className="num">P&amp;L</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {b.trade_list.map((t, i) => {
                      const sel = selectedTrade === i;
                      const reason = t.reason || t.exit_reason || '—';
                      return (
                        <tr key={i} onClick={() => setSelectedTrade(sel ? null : i)}
                          style={{ cursor: 'pointer', background: sel ? 'var(--accent-bg)' : undefined }}>
                          <td className="mono" style={{ fontSize: 10 }}>{t.entry_date}</td>
                          <td className="mono" style={{ fontSize: 10, color: 'var(--text-3)' }}>{t.exit_date || '—'}</td>
                          <td><Badge variant={t.side === 'BUY' ? 'pos' : 'neg'}>{t.side}</Badge></td>
                          <td className="num mono" style={{ fontSize: 10 }}>{t.days_held ?? '—'}</td>
                          <td className="num mono" style={{ color: (t.pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)', fontWeight: 600 }}>{fmtUsd(t.pnl, true)}</td>
                          <td style={{ fontSize: 10, color: 'var(--text-3)' }}>{reason}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* Equity + MC + WF */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div className="card">
              <CardHead icon="activity" title="Equity Curve" />
              <div style={{ height: 220, padding: '8px 12px 0' }}>
                {(b.equity || []).length > 0 ? (
                  <EquityChart data={b.equity} height={210} />
                ) : (
                  <div style={{ height: 210, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-3)', fontSize: 11 }}>
                    No equity curve yet
                  </div>
                )}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="card">
                <CardHead icon="radar" title="Monte Carlo" />
                <div className="card__body">
                  {b.mc ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      <Kpi label="Prob. of Profit" value={fmtPct(b.mc.prob_profit)} color="var(--pos)" />
                      <Kpi label="VaR 95%" value={fmtUsd(b.mc.p05)} color="var(--neg)" />
                      <Kpi label="Median Equity" value={fmtUsd(b.mc.p50)} color="var(--info)" />
                    </div>
                  ) : (
                    <div style={{ color: 'var(--text-3)', fontSize: 12 }}>Enable MC and run</div>
                  )}
                </div>
              </div>

              <div className="card">
                <CardHead icon="trending" title="Walk-Forward" />
                <div className="card__body" style={{ padding: 0 }}>
                  {(b.wf || []).length > 0 ? b.wf.map((w, i) => (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px var(--pad-x)', borderBottom: '1px solid var(--border-soft)', fontSize: 12 }}>
                      <span style={{ color: 'var(--text-3)' }}>W{i + 1}: {w.start_date}</span>
                      <span style={{ color: (w.pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)', fontWeight: 600 }}>{fmtUsd(w.pnl, true)}</span>
                    </div>
                  )) : (
                    <div style={{ padding: 'var(--pad-y) var(--pad-x)', color: 'var(--text-3)', fontSize: 12 }}>Enable WF and run</div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
