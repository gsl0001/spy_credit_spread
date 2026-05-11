import { useEffect, useState } from 'react';
import { api } from './api.js';

/**
 * Schema-driven strategy parameter form (use_request §3).
 *
 * Fetches /api/strategies/{id}/schema and renders an input per field.
 * Calls onChange({...currentValues, [field]: value}) on every edit so
 * the parent can merge into its config.
 *
 * Props:
 *   strategyId: 'consecutive_days' | 'combo_spread' | ...
 *   values:     dict of current param values (parent-owned)
 *   onChange:   (nextValues) => void
 */
export function StrategyParamsForm({ strategyId, values = {}, onChange }) {
  const [schema, setSchema] = useState({});
  const [err, setErr] = useState('');

  useEffect(() => {
    let cancelled = false;
    api.strategySchema(strategyId)
      .then(res => {
        if (cancelled) return;
        if (res.error) setErr(res.error);
        else {
          setErr('');
          setSchema(res.schema || {});
        }
      })
      .catch(e => !cancelled && setErr(String(e.message || e)));
    return () => { cancelled = true; };
  }, [strategyId]);

  const set = (k, v) => onChange && onChange({ ...values, [k]: v });

  const fields = Object.entries(schema);
  if (err) return <div className="muted" style={{ fontSize: 12 }}>schema error: {err}</div>;
  if (!fields.length) return <div className="muted" style={{ fontSize: 12 }}>no parameters</div>;

  return (
    <div className="strategy-param-grid">
      {fields.map(([key, def]) => {
        const val = values[key] ?? def.default ?? '';
        const label = def.label || key;
        return (
          <label key={key} style={{ display: 'flex', flexDirection: 'column', fontSize: 11 }}>
            <span className="muted">{label}</span>
            {def.type === 'boolean' ? (
              <div className="strategy-param-check">
                <input
                  type="checkbox"
                  checked={!!val}
                  onChange={e => set(key, e.target.checked)}
                />
                <span>{val ? 'Enabled' : 'Disabled'}</span>
              </div>
            ) : (
              <input
                type={def.type === 'number' ? 'number' : 'text'}
                value={val}
                min={def.min}
                max={def.max}
                step={def.step || (def.type === 'number' ? 1 : undefined)}
                onChange={e => set(key,
                  def.type === 'number' ? Number(e.target.value) : e.target.value)}
                style={{
                  background: 'var(--bg-2)', color: 'var(--text)',
                  border: '1px solid var(--border)', padding: '4px 6px',
                  borderRadius: 4, fontSize: 12,
                }}
              />
            )}
          </label>
        );
      })}
    </div>
  );
}
