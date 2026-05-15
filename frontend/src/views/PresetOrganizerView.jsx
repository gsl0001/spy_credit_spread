import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import { Badge, Btn } from '../primitives.jsx';
import { Ico } from '../icons.jsx';

const EMPTY_PRESET = {
  name: '',
  broker: 'moomoo',
  strategy_name: 'rsi2',
  bar_size: '1 day',
  ticker: 'SPY',
  strategy_type: 'bull_call',
  direction: 'bull',
  topology: 'vertical_spread',
  target_dte: 7,
  strike_width: 5,
  spread_cost_target: 250,
  stop_loss_pct: 50,
  take_profit_pct: 50,
  trailing_stop_pct: 0,
  commission_per_contract: 0.65,
  realism_factor: 1.15,
  use_mark_to_market: true,
  position_size_method: 'fixed',
  sizing_params: {
    fixed_contracts: 1,
    risk_percent: 1,
    max_allocation_cap: 250,
    capital_allocation: 10000,
  },
  auto_execute: false,
  bypass_event_blackout: false,
  fetch_only_live: true,
  timing_mode: 'interval',
  timing_value: 60,
  entry_filters: {
    use_rsi_filter: false,
    rsi_threshold: 30,
    use_ema_filter: false,
    ema_length: 10,
    use_sma200_filter: false,
    use_volume_filter: false,
    use_vix_filter: false,
    vix_min: 15,
    vix_max: 35,
    use_regime_filter: false,
    regime_allowed: 'all',
  },
  strategy_params: {},
  notes: '',
};

const ENTRY_FILTER_FIELDS = {
  use_rsi_filter: { type: 'boolean', label: 'Use RSI Filter' },
  rsi_threshold: { type: 'number', label: 'RSI Threshold' },
  use_ema_filter: { type: 'boolean', label: 'Use EMA Filter' },
  ema_length: { type: 'number', label: 'EMA Length' },
  use_sma200_filter: { type: 'boolean', label: 'Use SMA 200 Filter' },
  use_volume_filter: { type: 'boolean', label: 'Use Volume Filter' },
  use_vix_filter: { type: 'boolean', label: 'Use VIX Filter' },
  vix_min: { type: 'number', label: 'VIX Min' },
  vix_max: { type: 'number', label: 'VIX Max' },
  use_regime_filter: { type: 'boolean', label: 'Use Regime Filter' },
  regime_allowed: { type: 'string', label: 'Regime Allowed' },
};

const SIZING_FIELDS = {
  fixed_contracts: { type: 'number', label: 'Fixed Contracts' },
  risk_percent: { type: 'number', label: 'Risk %' },
  max_allocation_cap: { type: 'number', label: 'Max Allocation Cap' },
  capital_allocation: { type: 'number', label: 'Capital Allocation' },
};

function Field({ label, children, full }) {
  return (
    <div className="field" style={full ? { gridColumn: '1 / -1' } : undefined}>
      <label>{label}</label>
      {children}
    </div>
  );
}

function normalizedPreset(preset) {
  return {
    ...EMPTY_PRESET,
    ...(preset || {}),
    strategy_params: { ...(preset?.strategy_params || {}) },
    entry_filters: { ...EMPTY_PRESET.entry_filters, ...(preset?.entry_filters || {}) },
    sizing_params: { ...EMPTY_PRESET.sizing_params, ...(preset?.sizing_params || {}) },
  };
}

function PresetRow({ preset, active, onSelect }) {
  const isMoomoo = preset.broker === 'moomoo';
  return (
    <button
      type="button"
      onClick={onSelect}
      className="card"
      aria-current={active ? 'true' : undefined}
      style={{
        width: '100%',
        textAlign: 'left',
        padding: 10,
        borderColor: active ? 'var(--accent)' : 'var(--border)',
        background: active ? 'oklch(32% .04 160 / .55)' : 'var(--bg-1)',
        cursor: 'pointer',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <strong className="mono" style={{ fontSize: 12, lineHeight: 1.3, overflowWrap: 'anywhere' }}>{preset.name}</strong>
        <Badge variant={isMoomoo ? 'success' : 'neutral'}>{preset.broker || 'ibkr'}</Badge>
      </div>
      <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap', color: 'var(--text-3)', fontSize: 11 }}>
        <span>{preset.ticker}</span>
        <span>{preset.strategy_name}</span>
        <span>{preset.target_dte} DTE</span>
        {preset.auto_execute && <span style={{ color: 'var(--pos)' }}>auto</span>}
      </div>
    </button>
  );
}

export function PresetOrganizerView() {
  const [presets, setPresets] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [selectedName, setSelectedName] = useState('');
  const [draft, setDraft] = useState(() => normalizedPreset(EMPTY_PRESET));
  const [query, setQuery] = useState('');
  const [brokerFilter, setBrokerFilter] = useState('all');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [presetRes, strategyRes] = await Promise.all([api.presetsList(), api.strategies()]);
      const list = Array.isArray(presetRes) ? presetRes : (presetRes?.presets || []);
      setPresets(list);
      setStrategies(Array.isArray(strategyRes) ? strategyRes : []);
      if (!selectedName && list.length) {
        selectPreset(list[0]);
      }
    } catch (e) {
      setMsg(`Load failed: ${e.message || e}`);
    } finally {
      setBusy(false);
    }
  }, [selectedName]);

  useEffect(() => { refresh(); }, [refresh]);

  const filteredPresets = useMemo(() => {
    const q = query.trim().toLowerCase();
    return presets
      .filter(p => brokerFilter === 'all' || p.broker === brokerFilter)
      .filter(p => !q || [p.name, p.strategy_name, p.ticker, p.broker].some(v => String(v || '').toLowerCase().includes(q)))
      .sort((a, b) => String(a.name).localeCompare(String(b.name)));
  }, [presets, query, brokerFilter]);

  const strategyMeta = strategies.find(s => s.id === draft.strategy_name);
  const set = (key, value) => setDraft(prev => ({ ...prev, [key]: value }));
  const num = key => e => set(key, Number(e.target.value));
  const str = key => e => set(key, e.target.value);
  const bool = key => e => set(key, e.target.checked);

  const selectPreset = preset => {
    const next = normalizedPreset(preset);
    setSelectedName(next.name);
    setDraft(next);
    setMsg('');
  };

  const createNew = () => {
    const next = normalizedPreset({ ...EMPTY_PRESET, name: `new-preset-${Date.now().toString().slice(-5)}` });
    setSelectedName('');
    setDraft(next);
    setMsg('New preset draft. Save when ready.');
  };

  const cloneCurrent = () => {
    const next = normalizedPreset({ ...draft, name: `${draft.name || 'preset'}-copy`, auto_execute: false });
    setSelectedName('');
    setDraft(next);
    setMsg('Cloned as draft with auto_execute off.');
  };

  const save = async () => {
    if (!draft.name.trim()) {
      setMsg('Preset name is required.');
      return;
    }
    setBusy(true);
    setMsg('');
    try {
      const payload = {
        ...draft,
        name: draft.name.trim(),
        bar_size: strategyMeta?.bar_size || draft.bar_size || '1 day',
      };
      const res = await api.presetSave(payload);
      if (res.error) {
        setMsg(`Save failed: ${res.detail || res.error}`);
      } else {
        const saved = normalizedPreset(res.preset || payload);
        selectPreset(saved);
        setMsg(`Saved ${saved.name}.`);
        const pRes = await api.presetsList();
        setPresets(Array.isArray(pRes) ? pRes : (pRes?.presets || []));
      }
    } catch (e) {
      setMsg(`Save failed: ${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!draft.name || !window.confirm(`Delete preset ${draft.name}?`)) return;
    setBusy(true);
    try {
      const res = await api.presetDelete(draft.name);
      if (res.error) {
        setMsg(`Delete failed: ${res.error}`);
      } else {
        setMsg(`Deleted ${draft.name}.`);
        setSelectedName('');
        setDraft(normalizedPreset(EMPTY_PRESET));
        const pRes = await api.presetsList();
        setPresets(Array.isArray(pRes) ? pRes : (pRes?.presets || []));
      }
    } catch (e) {
      setMsg(`Delete failed: ${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const setStrategyParam = (key, value) => {
    setDraft(prev => ({
      ...prev,
      strategy_params: { ...(prev.strategy_params || {}), [key]: value },
    }));
  };

  const setEntryFilter = (key, value) => {
    setDraft(prev => ({
      ...prev,
      entry_filters: { ...(prev.entry_filters || {}), [key]: value },
    }));
  };

  const setSizingParam = (key, value) => {
    setDraft(prev => ({
      ...prev,
      sizing_params: { ...(prev.sizing_params || {}), [key]: value },
    }));
  };

  const renderParamInput = (key, def, value, onChange) => {
    const label = def?.label || key;
    if (def?.type === 'boolean' || typeof value === 'boolean') {
      return (
        <Field key={key} label={label}>
          <label className="strategy-param-check">
            <input type="checkbox" checked={!!value} onChange={e => onChange(key, e.target.checked)} />
            <span>{value ? 'Enabled' : 'Disabled'}</span>
          </label>
        </Field>
      );
    }
    const isNumber = def?.type === 'number' || typeof value === 'number';
    return (
      <Field key={key} label={label}>
        <input
          className="inp"
          type={isNumber ? 'number' : 'text'}
          value={value ?? ''}
          min={def?.min}
          max={def?.max}
          step={def?.step || (isNumber ? 'any' : undefined)}
          onChange={e => onChange(key, isNumber ? Number(e.target.value) : e.target.value)}
        />
      </Field>
    );
  };

  return (
    <div className="preset-organizer">
      <div className="preset-organizer__sidebar">
        <div className="card preset-organizer__filters">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 10 }}>
            <div style={{ fontWeight: 700, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Ico name="book" size={14} /> Presets
            </div>
            <Btn size="sm" variant="primary" onClick={createNew}><Ico name="plus" size={12} /> New</Btn>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 110px', gap: 8 }}>
            <input className="inp" placeholder="Search presets" value={query} onChange={e => setQuery(e.target.value)} />
            <select className="sel" value={brokerFilter} onChange={e => setBrokerFilter(e.target.value)}>
              <option value="all">All</option>
              <option value="moomoo">Moomoo</option>
              <option value="ibkr">IBKR</option>
            </select>
          </div>
        </div>
        <div className="preset-organizer__list">
          {filteredPresets.map(p => (
            <PresetRow key={p.name} preset={p} active={selectedName === p.name} onSelect={() => selectPreset(p)} />
          ))}
          {filteredPresets.length === 0 && (
            <div className="card" style={{ padding: 18, color: 'var(--text-3)', fontSize: 12, textAlign: 'center' }}>
              No presets match this filter.
            </div>
          )}
        </div>
      </div>

      <div className="preset-organizer__editor">
        <div className="card">
          <div className="card__head">
            <span className="title"><Ico name="sliders" size={13} /> Preset Editor</span>
            <div className="card__actions">
              <Btn size="sm" variant="ghost" onClick={refresh} disabled={busy}>Refresh</Btn>
              <Btn size="sm" variant="ghost" onClick={cloneCurrent} disabled={busy || !draft.name}>Clone</Btn>
              <Btn size="sm" variant="ghost" onClick={remove} disabled={busy || !selectedName}>Delete</Btn>
              <Btn size="sm" variant="primary" onClick={save} disabled={busy}>{busy ? 'Saving' : 'Save'}</Btn>
            </div>
          </div>
          {msg && (
            <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--border-soft)', color: msg.includes('failed') || msg.includes('required') ? 'var(--neg)' : 'var(--text-2)', fontSize: 12 }}>
              {msg}
            </div>
          )}
          <div className="card__body" style={{ display: 'grid', gap: 18 }}>
            <section>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <strong style={{ fontSize: 13 }}>Identity</strong>
                <div style={{ display: 'flex', gap: 6 }}>
                  <Badge variant={draft.auto_execute ? 'success' : 'neutral'}>{draft.auto_execute ? 'auto on' : 'auto off'}</Badge>
                  <Badge variant={draft.broker === 'moomoo' ? 'success' : 'neutral'}>{draft.broker}</Badge>
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
                <Field label="Name" full><input className="inp mono" value={draft.name} onChange={str('name')} /></Field>
                <Field label="Broker"><select className="sel" value={draft.broker} onChange={str('broker')}><option value="moomoo">moomoo</option><option value="ibkr">ibkr</option></select></Field>
                <Field label="Ticker"><input className="inp mono" value={draft.ticker} onChange={e => set('ticker', e.target.value.toUpperCase())} /></Field>
                <Field label="Strategy">
                  <select className="sel" value={draft.strategy_name} onChange={e => set('strategy_name', e.target.value)}>
                    {strategies.map(s => <option key={s.id} value={s.id}>{s.id}{s.vetting_result === 'rejected' ? ' (rejected)' : ''}</option>)}
                  </select>
                </Field>
                <Field label="Bar Size"><input className="inp" value={strategyMeta?.bar_size || draft.bar_size || '1 day'} onChange={str('bar_size')} /></Field>
              </div>
            </section>

            <section>
              <strong style={{ fontSize: 13 }}>Execution</strong>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, marginTop: 10 }}>
                <Field label="Auto Execute"><label className="strategy-param-check"><input type="checkbox" checked={!!draft.auto_execute} onChange={bool('auto_execute')} /><span>{draft.auto_execute ? 'Enabled' : 'Disabled'}</span></label></Field>
                <Field label="Fetch Only Live"><label className="strategy-param-check"><input type="checkbox" checked={!!draft.fetch_only_live} onChange={bool('fetch_only_live')} /><span>{draft.fetch_only_live ? 'Enabled' : 'Disabled'}</span></label></Field>
                <Field label="Bypass Event Blackout"><label className="strategy-param-check"><input type="checkbox" checked={!!draft.bypass_event_blackout} onChange={bool('bypass_event_blackout')} /><span>{draft.bypass_event_blackout ? 'Enabled' : 'Disabled'}</span></label></Field>
                <Field label="Timing Mode"><select className="sel" value={draft.timing_mode} onChange={str('timing_mode')}><option value="interval">interval</option><option value="after_open">after_open</option><option value="before_close">before_close</option><option value="on_open">on_open</option><option value="on_close">on_close</option></select></Field>
                <Field label="Timing Value"><input className="inp" type="number" value={draft.timing_value} onChange={num('timing_value')} /></Field>
              </div>
            </section>

            <section>
              <strong style={{ fontSize: 13 }}>Option Structure</strong>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: 10, marginTop: 10 }}>
                <Field label="Topology"><select className="sel" value={draft.topology} onChange={str('topology')}><option value="vertical_spread">vertical_spread</option><option value="long_call">long_call</option><option value="straddle">straddle</option><option value="iron_condor">iron_condor</option><option value="butterfly">butterfly</option></select></Field>
                <Field label="Direction"><select className="sel" value={draft.direction} onChange={str('direction')}><option value="bull">bull</option><option value="bear">bear</option><option value="neutral">neutral</option></select></Field>
                <Field label="Strategy Type"><select className="sel" value={draft.strategy_type} onChange={str('strategy_type')}><option value="bull_call">bull_call</option><option value="bear_put">bear_put</option><option value="bull_put">bull_put</option><option value="bear_call">bear_call</option></select></Field>
                <Field label="DTE"><input className="inp" type="number" value={draft.target_dte} onChange={num('target_dte')} /></Field>
                <Field label="Strike Width"><input className="inp" type="number" value={draft.strike_width} onChange={num('strike_width')} /></Field>
                <Field label="Spread Target"><input className="inp" type="number" value={draft.spread_cost_target} onChange={num('spread_cost_target')} /></Field>
                <Field label="Stop Loss %"><input className="inp" type="number" value={draft.stop_loss_pct} onChange={num('stop_loss_pct')} /></Field>
                <Field label="Take Profit %"><input className="inp" type="number" value={draft.take_profit_pct} onChange={num('take_profit_pct')} /></Field>
                <Field label="Commission"><input className="inp" type="number" step="0.01" value={draft.commission_per_contract} onChange={num('commission_per_contract')} /></Field>
              </div>
            </section>

            <section>
              <strong style={{ fontSize: 13 }}>Strategy Params</strong>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10, marginTop: 10 }}>
                {Object.entries(strategyMeta?.schema || {}).map(([key, def]) => (
                  renderParamInput(
                    key,
                    def,
                    draft.strategy_params?.[key] ?? def.default ?? '',
                    setStrategyParam,
                  )
                ))}
                {Object.keys(strategyMeta?.schema || {}).length === 0 && (
                  <div className="muted" style={{ fontSize: 12 }}>This strategy has no editable parameters.</div>
                )}
              </div>
            </section>

            <section>
              <strong style={{ fontSize: 13 }}>Entry Filters</strong>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10, marginTop: 10 }}>
                {Object.entries(ENTRY_FILTER_FIELDS).map(([key, def]) => (
                  renderParamInput(key, def, draft.entry_filters?.[key] ?? EMPTY_PRESET.entry_filters[key] ?? '', setEntryFilter)
                ))}
              </div>
            </section>

            <section>
              <strong style={{ fontSize: 13 }}>Sizing</strong>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10, marginTop: 10 }}>
                <Field label="Position Size Method">
                  <select className="sel" value={draft.position_size_method} onChange={str('position_size_method')}>
                    <option value="fixed">fixed</option>
                    <option value="dynamic_risk">dynamic_risk</option>
                    <option value="targeted_spread">targeted_spread</option>
                  </select>
                </Field>
                {Object.entries(SIZING_FIELDS).map(([key, def]) => (
                  renderParamInput(key, def, draft.sizing_params?.[key] ?? EMPTY_PRESET.sizing_params[key] ?? 0, setSizingParam)
                ))}
              </div>
            </section>

            <section>
              <Field label="Notes" full><textarea className="inp" rows={4} value={draft.notes} onChange={str('notes')} /></Field>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
