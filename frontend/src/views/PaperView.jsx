import { useCallback, useEffect, useMemo, useState } from 'react';
import { fmtUsd, Card, Kpi, Badge, Btn } from '../primitives.jsx';
import { api } from '../api.js';

// Paper-trading gate — automated multi-preset trialing on moomoo paper.
//
// Each row = one preset under trial. The backend's daily evaluator job
// transitions trialing → passed/failed when the sample-size minimums
// (min_trades + min_days) are met. Promotion is manual via the button.

function VerdictPill({ verdict }) {
  const variant =
    verdict === 'pass' ? 'success' :
    verdict === 'fail' ? 'danger' :
    verdict === 'warn' ? 'warning' : 'neutral';
  return <Badge variant={variant}>{verdict || 'pending'}</Badge>;
}

function StatusPill({ status }) {
  const variant =
    status === 'trialing' ? 'info' :
    status === 'passed' ? 'success' :
    status === 'failed' ? 'danger' :
    status === 'promoted' ? 'success' :
    'neutral';
  return <Badge variant={variant}>{status}</Badge>;
}

function StartTrialForm({ presets, onStarted }) {
  const [presetName, setPresetName] = useState('');
  const [wr, setWr] = useState('');
  const [cadence, setCadence] = useState('');
  const [maxDd, setMaxDd] = useState('');
  const [minTrades, setMinTrades] = useState(20);
  const [minDays, setMinDays] = useState(7);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const submit = useCallback(async () => {
    if (!presetName) { setMsg('Pick a preset.'); return; }
    setBusy(true); setMsg('');
    try {
      const res = await api.paperTrialStart({
        preset_name: presetName,
        expected_win_rate_pct: Number(wr) || 0,
        expected_trades_per_week: Number(cadence) || 0,
        expected_max_drawdown_pct: Number(maxDd) || 0,
        min_trades: Number(minTrades) || 20,
        min_days: Number(minDays) || 7,
      });
      if (res.error) {
        setMsg(`Failed: ${res.error}`);
      } else {
        setMsg(`Trial started for ${presetName}.`);
        setPresetName(''); setWr(''); setCadence(''); setMaxDd('');
        onStarted && onStarted();
      }
    } catch (e) {
      setMsg(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }, [presetName, wr, cadence, maxDd, minTrades, minDays, onStarted]);

  return (
    <Card title="Start Trial" icon="zap">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 }}>
        <div className="field">
          <label>Preset</label>
          <select className="sel" value={presetName} onChange={e => setPresetName(e.target.value)}>
            <option value="">— pick —</option>
            {(presets || []).map(p => (
              <option key={p.name} value={p.name}>
                {p.name} · {p.strategy_name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Expected WR %</label>
          <input className="inp" type="number" step="0.1" value={wr}
            onChange={e => setWr(e.target.value)} placeholder="e.g. 75" />
        </div>
        <div className="field">
          <label>Trades / week</label>
          <input className="inp" type="number" step="0.1" value={cadence}
            onChange={e => setCadence(e.target.value)} placeholder="e.g. 0.5" />
        </div>
        <div className="field">
          <label>Max DD % (negative)</label>
          <input className="inp" type="number" step="0.1" value={maxDd}
            onChange={e => setMaxDd(e.target.value)} placeholder="e.g. -4" />
        </div>
        <div className="field">
          <label>Min trades</label>
          <input className="inp" type="number" value={minTrades}
            onChange={e => setMinTrades(e.target.value)} />
        </div>
        <div className="field">
          <label>Min days</label>
          <input className="inp" type="number" value={minDays}
            onChange={e => setMinDays(e.target.value)} />
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10 }}>
        <Btn variant="primary" onClick={submit} disabled={busy}>
          {busy ? 'Starting…' : 'Start Trial'}
        </Btn>
        {msg && <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{msg}</span>}
      </div>
    </Card>
  );
}

function TrialRow({ entry, onAction }) {
  const { trial, evaluation } = entry;
  const live = evaluation?.live || {};
  const expected = evaluation?.expected || {};
  const findings = evaluation?.findings || [];
  const tradeRatio = expected.trades_per_week > 0
    ? `${live.trades_per_week ?? 0} / ${expected.trades_per_week}/wk`
    : `${live.trades_per_week ?? 0}/wk`;
  const wrCell = `${live.win_rate_pct ?? 0}% ${expected.win_rate_pct ? `(target ${expected.win_rate_pct}%)` : ''}`;
  return (
    <tr>
      <td className="mono" style={{ fontWeight: 700 }}>{trial.preset_name}</td>
      <td><StatusPill status={trial.status} /></td>
      <td><VerdictPill verdict={evaluation?.verdict} /></td>
      <td className="mono">{evaluation?.days_open ?? 0}d / {trial.min_days}d</td>
      <td className="mono">{live.positions_closed ?? 0} / {trial.min_trades}</td>
      <td className="mono">{wrCell}</td>
      <td className="mono">{tradeRatio}</td>
      <td className="mono">{fmtUsd(live.biggest_loss_dollars ?? 0)}</td>
      <td>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {trial.status === 'trialing' && (
            <Btn size="sm" variant="ghost" onClick={() => onAction('stop', trial.preset_name)}>Stop</Btn>
          )}
          {trial.status === 'passed' && (
            <Btn size="sm" variant="primary" onClick={() => onAction('promote', trial.preset_name)}>Promote</Btn>
          )}
          <Btn size="sm" variant="ghost" onClick={() => onAction('delete', trial.preset_name)}>×</Btn>
        </div>
        {findings.length > 0 && (
          <div style={{ marginTop: 4, fontSize: 11, color: 'var(--text-4)' }}>
            {findings.map((f, i) => (
              <div key={i}>· {f.message}</div>
            ))}
          </div>
        )}
      </td>
    </tr>
  );
}

export function PaperView() {
  const [entries, setEntries] = useState([]);
  const [presets, setPresets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState('');
  const [lastEvalMsg, setLastEvalMsg] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [trialRes, presetRes] = await Promise.all([
        api.paperTrialsList(filterStatus || undefined),
        api.presetsList(),
      ]);
      setEntries(trialRes?.trials || []);
      setPresets(Array.isArray(presetRes) ? presetRes : (presetRes?.presets || []));
    } catch (e) {
      console.error('paper trials refresh', e);
    } finally {
      setLoading(false);
    }
  }, [filterStatus]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    const id = setInterval(refresh, 30000);
    return () => clearInterval(id);
  }, [refresh]);

  const evaluateNow = useCallback(async () => {
    setLastEvalMsg('Evaluating…');
    try {
      const res = await api.paperTrialEvaluate();
      const n = res?.count ?? 0;
      const trans = res?.transitions || [];
      setLastEvalMsg(`Evaluated ${n} trial${n === 1 ? '' : 's'}` +
        (trans.length ? ` · ${trans.length} transition${trans.length === 1 ? '' : 's'}` : ''));
      refresh();
    } catch (e) {
      setLastEvalMsg(`Failed: ${e.message || e}`);
    }
  }, [refresh]);

  const handleAction = useCallback(async (action, presetName) => {
    if (action === 'stop') {
      if (!confirm(`Stop trial for ${presetName}? This disables auto_execute.`)) return;
      await api.paperTrialStop(presetName);
    } else if (action === 'promote') {
      await api.paperTrialPromote(presetName);
    } else if (action === 'delete') {
      if (!confirm(`Delete trial row for ${presetName}? (does not delete the preset)`)) return;
      await api.paperTrialDelete(presetName);
    }
    refresh();
  }, [refresh]);

  const counts = useMemo(() => {
    const c = { trialing: 0, passed: 0, failed: 0, promoted: 0, demoted: 0 };
    entries.forEach(e => { c[e.trial.status] = (c[e.trial.status] || 0) + 1; });
    return c;
  }, [entries]);

  return (
    <div className="workspace-main">
      <div className="responsive-grid responsive-grid-kpi">
        <Kpi label="Trialing" value={counts.trialing || 0} />
        <Kpi label="Passed" value={counts.passed || 0} />
        <Kpi label="Failed" value={counts.failed || 0} />
        <Kpi label="Promoted" value={counts.promoted || 0} />
      </div>

      <StartTrialForm presets={presets} onStarted={refresh} />

      <Card
        title="Active Trials"
        icon="radar"
        actions={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <select className="sel" value={filterStatus}
              onChange={e => setFilterStatus(e.target.value)}>
              <option value="">all</option>
              <option value="trialing">trialing</option>
              <option value="passed">passed</option>
              <option value="failed">failed</option>
              <option value="promoted">promoted</option>
              <option value="demoted">demoted</option>
            </select>
            <Btn size="sm" variant="ghost" onClick={evaluateNow}>Evaluate Now</Btn>
            <Btn size="sm" variant="ghost" onClick={refresh} disabled={loading}>
              {loading ? '…' : 'Refresh'}
            </Btn>
          </div>
        }
      >
        {lastEvalMsg && (
          <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 8 }}>{lastEvalMsg}</div>
        )}
        {entries.length === 0 ? (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-4)', fontSize: 12 }}>
            No trials yet. Start one above — pick a preset and paste its backtest WR / cadence / max DD.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  <th>Preset</th>
                  <th>Status</th>
                  <th>Verdict</th>
                  <th>Days</th>
                  <th>Trades</th>
                  <th>Win Rate</th>
                  <th>Cadence</th>
                  <th>Worst Loss</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(e => (
                  <TrialRow key={e.trial.preset_name} entry={e} onAction={handleAction} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="How this works" icon="book">
        <div style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--text-2)' }}>
          <p>
            Start a trial for a preset and supply its <strong>backtest</strong> stats
            (win rate, trades/week, max drawdown%). The auto-execute path fires
            normally but enforces <strong>fixed contracts = 1</strong> and the per-trial
            position cap so live samples stay apples-to-apples with the backtest.
          </p>
          <p>
            A daily job at <strong>16:30 ET</strong> evaluates each trial and transitions:
          </p>
          <ul style={{ marginLeft: 16 }}>
            <li><code>passed</code> — verdict <code>pass</code> after ≥ min_trades AND ≥ min_days</li>
            <li><code>failed</code> — verdict <code>fail</code> at sample-size readiness, OR a single
              loss exceeds 2× backtest max DD (catastrophic short-circuit)</li>
            <li><code>demoted</code> — manual stop, disables auto_execute on the preset</li>
            <li><code>promoted</code> — human review only; click <em>Promote</em> on a passed trial</li>
          </ul>
          <p>
            Promotion is an audit flag — it does not move funds or switch broker
            accounts. To send the validated preset to a live account, clone it
            via the Moomoo view with <code>trd_env=REAL</code>.
          </p>
        </div>
      </Card>
    </div>
  );
}
