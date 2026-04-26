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
  trade_list: [], mc: null, wf: [], price_history: [],
};

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
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      fontSize: 12, gridColumn: '1 / -1', padding: '3px 0',
    }}>
      <span style={{ color: on ? 'var(--text)' : 'var(--text-3)' }}>{label}</span>
      <Switch on={!!on} onChange={onChange} />
    </div>
  );
}

function SectionBox({ title, icon, children, open = false }) {
  const [isOpen, setIsOpen] = useState(open);
  return (
    <div style={{ border: '1px solid var(--border-soft)', borderRadius: 6, overflow: 'hidden' }}>
      <div
        onClick={() => setIsOpen(v => !v)}
        style={{
          padding: '7px 10px', cursor: 'pointer', userSelect: 'none',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          background: 'var(--bg-2)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-2)' }}>
          <Ico name={icon} size={12} /> {title}
        </div>
        <Ico name={isOpen ? 'minus' : 'plus'} size={11} />
      </div>
      {isOpen && (
        <div style={{ padding: '10px 10px 12px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, background: 'var(--bg-1)' }}>
          {children}
        </div>
      )}
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
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, padding: '5px 0', borderBottom: '1px solid var(--border-soft)' }}>
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

  const update = (key, val) => setConfig(prev => ({ ...prev, [key]: val }));
  const num = key => e => update(key, Number(e.target.value));
  const str = key => e => update(key, e.target.value);
  const bool = key => v => update(key, v);

  const allPresets = useMemo(() => ({ ...BUILT_IN_PRESETS, ...presets }), [presets]);

  const applyPreset = name => {
    setSelectedPreset(name);
    if (name && allPresets[name]) setConfig({ ...DEFAULT_CONFIG, ...allPresets[name] });
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

  return (
    // .page → flex: 1; overflow: auto; — the whole view scrolls naturally
    <div className="page" style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 12 }}>

      {/* ── KPI strip ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', gap: 8 }}>
        {[
          { label: 'Total Return',   value: fmtPct(b.total_return_pct), color: b.total_return_pct >= 0 ? 'var(--pos)' : 'var(--neg)' },
          { label: 'Max Drawdown',   value: fmtPct(b.max_dd_pct),        color: 'var(--neg)' },
          { label: 'Sharpe Ratio',   value: (b.sharpe || 0).toFixed(2) },
          { label: 'Profit Factor',  value: (b.profit_factor || 0).toFixed(2) },
          { label: 'Win Rate',       value: Math.round((b.win_rate || 0) * 100) + '%' },
          { label: 'Trade Count',    value: b.trades || 0 },
        ].map(({ label, value, color }) => (
          <div key={label} className="card">
            <div className="card__body"><Kpi label={label} value={value} color={color} big /></div>
          </div>
        ))}
      </div>

      {/* ── 3-column body — no viewport locking, grows with content ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr 288px', gap: 12, alignItems: 'start' }}>

        {/* ══ LEFT: config panel — natural flow, no internal scroll ══ */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>

          <div style={{ display: 'flex', gap: 6 }}>
            <button
              className="btn primary"
              onClick={run}
              disabled={loading}
              style={{ flex: 1, justifyContent: 'center', height: 36, fontSize: 12, fontWeight: 700, gap: 6 }}
            >
              <Ico name="zap" size={13} /> {loading ? 'CALCULATING…' : 'RUN SIMULATION'}
            </button>
            <Btn size="sm" variant="ghost" onClick={() => setConfig({ ...DEFAULT_CONFIG })} style={{ whiteSpace: 'nowrap' }}>Reset</Btn>
          </div>

          <SectionBox title="Asset & Timeframe" icon="cog" open>
            <Field label="Ticker">
              <input className="inp" value={config.ticker || ''} onChange={str('ticker')} />
            </Field>
            <Field label="History (yrs)">
              <input className="inp" type="number" value={config.years_history || 0} onChange={num('years_history')} />
            </Field>
            <Field label="Initial Capital $">
              <input className="inp" type="number" value={config.capital_allocation || 0} onChange={num('capital_allocation')} />
            </Field>
            <Field label="Commission $">
              <input className="inp" type="number" step="0.01" value={config.commission_per_contract || 0} onChange={num('commission_per_contract')} />
            </Field>
          </SectionBox>

          <SectionBox title="Position Sizing" icon="target" open>
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
          </SectionBox>

          <SectionBox title="Strategy Engine" icon="activity" open>
            <Field label="Logic" full>
              <select className="sel" value={config.strategy_id || ''} onChange={str('strategy_id')}>
                {strategies.length === 0 && <option value="">— loading… —</option>}
                {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>
            <div style={{ gridColumn: '1/-1' }}>
              <StrategyParamsForm
                strategyId={config.strategy_id || 'consecutive_days'}
                values={config}
                onChange={v => setConfig(prev => ({ ...prev, ...v }))}
              />
            </div>
          </SectionBox>

          <SectionBox title="Execution Details" icon="zap">
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
          </SectionBox>

          <SectionBox title="Risk Management" icon="shield">
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
          </SectionBox>

          <SectionBox title="Entry Filters" icon="sliders">
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
          </SectionBox>

          <SectionBox title="Advanced / Analysis" icon="radar">
            <Toggle label="Monte Carlo" on={!!config.enable_mc_histogram} onChange={bool('enable_mc_histogram')} />
            <Toggle label="Walk-Forward" on={!!config.enable_walk_forward} onChange={bool('enable_walk_forward')} />
            {config.enable_walk_forward && (
              <Field label="WF Windows" full>
                <input className="inp" type="number" min="2" max="12" value={config.walk_forward_windows ?? 4} onChange={num('walk_forward_windows')} />
              </Field>
            )}
          </SectionBox>

          {/* Presets card */}
          <div className="card">
            <CardHead icon="book" title="Presets" />
            <div className="card__body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <select className="sel" value={selectedPreset || ''} onChange={e => applyPreset(e.target.value)}>
                <option value="">— Load preset —</option>
                {Object.keys(allPresets).map(n => <option key={n} value={n}>{n}</option>)}
              </select>
              {selectedPreset && presets[selectedPreset] && (
                <Btn size="sm" variant="ghost" icon="x" onClick={() => deletePreset(selectedPreset)} style={{ color: 'var(--neg)' }}>
                  Delete "{selectedPreset}"
                </Btn>
              )}
              <div style={{ display: 'flex', gap: 6 }}>
                <input
                  className="inp"
                  placeholder="Save current as…"
                  value={presetName}
                  onChange={e => setPresetName(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && savePreset()}
                  style={{ flex: 1 }}
                />
                <Btn size="sm" variant="primary" onClick={savePreset} disabled={!presetName.trim()}>Save</Btn>
              </div>
            </div>
          </div>

          <OptimiserCard baseConfig={config} />
        </div>

        {/* ══ CENTER: charts, fixed heights so they're always usable ══ */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>

          <div className="card" style={{ display: 'flex', flexDirection: 'column' }}>
            <CardHead
              icon="trending"
              title={`Price & Trades${b.trades ? ` · ${b.trades} trades` : ''}`}
              actions={selectedTrade && (
                <button
                  onClick={() => setSelectedTrade(null)}
                  style={{ background: 'none', border: 0, color: 'var(--text-3)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, fontSize: 11 }}
                >
                  <Ico name="x" size={11} /> Clear selection
                </button>
              )}
            />
            {error && (
              <div style={{ padding: '6px 12px', color: 'var(--neg)', background: 'oklch(38% .18 20 / 0.15)', borderBottom: '1px solid var(--border-soft)', fontSize: 12 }}>
                {error}
              </div>
            )}
            <div style={{ height: 420 }}>
              <CandlestickChart
                series={b.price_history}
                trades={b.trade_list}
                height="100%"
                selectedTrade={selectedTrade}
                onTradeSelect={setSelectedTrade}
              />
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 10 }}>

            <div className="card" style={{ display: 'flex', flexDirection: 'column' }}>
              <CardHead icon="activity" title="Trade Log" />
              <div style={{ maxHeight: 260, overflowY: 'auto' }}>
                {b.trade_list.length === 0 ? (
                  <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>
                    No trades yet — run a simulation
                  </div>
                ) : (
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Side</th>
                        <th className="num">P&amp;L</th>
                        <th>Exit Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {b.trade_list.map((t, i) => {
                        const isSelected = selectedTrade === i;
                        return (
                          <tr
                            key={i}
                            onClick={() => setSelectedTrade(isSelected ? null : i)}
                            style={{
                              cursor: 'pointer',
                              background: isSelected ? 'var(--accent-bg)' : undefined,
                            }}
                          >
                            <td className="mono" style={{ fontSize: 10 }}>{t.entry_date}</td>
                            <td><Badge variant={t.side === 'BUY' ? 'pos' : 'neg'}>{t.side}</Badge></td>
                            <td className="num mono" style={{ color: (t.pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)', fontWeight: 600 }}>{fmtUsd(t.pnl, true)}</td>
                            <td style={{ fontSize: 10, color: 'var(--text-3)' }}>{t.exit_reason || '—'}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </div>

            <div className="card" style={{ display: 'flex', flexDirection: 'column' }}>
              <CardHead icon="activity" title="Equity Curve" />
              <div style={{ height: 200, padding: '8px 12px 0' }}>
                <EquityChart data={b.equity} height={192} />
              </div>
              <div style={{ padding: '8px var(--pad-x)', borderTop: '1px solid var(--border-soft)' }}>
                <Kpi label="Avg Trade P&L" value={fmtUsd(b.avg_pnl, true)} color={(b.avg_pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)'} />
              </div>
            </div>

          </div>
        </div>

        {/* ══ RIGHT: analytics ══ */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

          <div className="card">
            <CardHead icon="radar" title="Monte Carlo" />
            <div className="card__body">
              {b.mc ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <Kpi label="Prob. of Profit"  value={fmtPct(b.mc.prob_profit)}  color="var(--pos)" />
                  <Kpi label="VaR 95% (equity)" value={fmtUsd(b.mc.p05)}          color="var(--neg)" />
                  <Kpi label="Median Equity"    value={fmtUsd(b.mc.p50)}          color="var(--info)" />
                </div>
              ) : (
                <div style={{ color: 'var(--text-3)', fontSize: 12 }}>Enable Monte Carlo and run</div>
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
                <div style={{ padding: 'var(--pad-y) var(--pad-x)', color: 'var(--text-3)', fontSize: 12 }}>Enable walk-forward and run</div>
              )}
            </div>
          </div>

          <div className="card">
            <CardHead icon="dashboard" title="Execution Stats" />
            <div className="card__body" style={{ padding: '4px var(--pad-x)' }}>
              <StatRow label="Total P&L"       value={fmtUsd(b.total_pnl, true)} color={(b.total_pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)'} />
              <StatRow label="Avg P&L / trade" value={fmtUsd(b.avg_pnl, true)}   color={(b.avg_pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)'} />
              <StatRow label="Win Rate"        value={Math.round((b.win_rate || 0) * 100) + '%'} />
              <StatRow label="Profit Factor"   value={(b.profit_factor || 0).toFixed(2)} />
              <StatRow label="Sharpe Ratio"    value={(b.sharpe || 0).toFixed(2)} />
              <StatRow
                label="Expectancy"
                value={((b.win_rate || 0) * (b.avg_pnl || 0)).toFixed(2)}
                color={((b.win_rate || 0) * (b.avg_pnl || 0)) >= 0 ? 'var(--pos)' : 'var(--neg)'}
              />
              <StatRow
                label="Recovery Factor"
                value={((b.total_pnl || 0) / (Math.abs((b.max_dd_pct || 0) * 100) || 1)).toFixed(2)}
              />
            </div>
          </div>

        </div>

      </div>
    </div>
  );
}
