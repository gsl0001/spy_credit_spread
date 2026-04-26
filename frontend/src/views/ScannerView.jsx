import { useState, useEffect } from 'react';
import { useData } from '../useBackendData.jsx';
import { Card, Btn, Pill, Switch, Chip } from '../primitives.jsx';
import { api, safe, IBKR_CREDS } from '../api.js';
import { loadConfig } from '../backtestConfig.js';

const TIMING_OPTIONS = [
  { mode: 'interval', value: 15, label: 'Every 15 sec' },
  { mode: 'interval', value: 60, label: 'Every 60 sec' },
  { mode: 'interval', value: 300, label: 'Every 5 min' },
  { mode: 'after_open', value: 5, label: '5 min after open' },
  { mode: 'after_open', value: 30, label: '30 min after open' },
  { mode: 'before_close', value: 15, label: '15 min before close' },
  { mode: 'on_open', value: 0, label: 'On market open' },
  { mode: 'on_close', value: 0, label: 'On market close' },
];

export function ScannerView() {
  const s = useData().scanner;
  const [mode, setMode] = useState('paper');            // "paper" | "ibkr"
  const [timingKey, setTimingKey] = useState('interval:60');
  const [rsiThreshold, setRsiThreshold] = useState(30);
  const [emaLength, setEmaLength] = useState(10);
  const [autoExecute, setAutoExecute] = useState(false);
  const [persist, setPersist] = useState(true);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (s?.mode) setMode(s.mode.toLowerCase());
  }, [s?.mode]);

  const [timingMode, timingValueStr] = timingKey.split(':');
  const timingValue = Number(timingValueStr);

  const handleStart = async () => {
    setBusy(true); setStatus(null);
    try {
      const cfg = { ...loadConfig(), rsi_threshold: rsiThreshold, ema_length: emaLength };
      const creds = mode === 'ibkr'
        ? IBKR_CREDS
        : { api_key: localStorage.getItem('alpaca_key') || '', api_secret: localStorage.getItem('alpaca_secret') || '' };
      const res = await api.scannerStart({
        timing_mode: timingMode,
        timing_value: timingValue,
        mode,
        auto_execute: autoExecute,
        config: cfg,
        creds,
      });
      setStatus(`Scanner started (${res.timing_mode}/${res.timing_value})`);
    } catch (e) { setStatus(`Error: ${e.message}`); }
    finally { setBusy(false); }
  };

  const handleStop = async () => {
    setBusy(true); setStatus(null);
    try {
      await api.scannerStop();
      setStatus('Scanner stopped');
    } catch (e) { setStatus(`Error: ${e.message}`); }
    finally { setBusy(false); }
  };

  const handleScanNow = async () => {
    setBusy(true); setStatus('Scanning…');
    try {
      const cfg = { ...loadConfig(), rsi_threshold: rsiThreshold, ema_length: emaLength };
      if (mode === 'paper') {
        const key = localStorage.getItem('alpaca_key') || '';
        const secret = localStorage.getItem('alpaca_secret') || '';
        const res = await api.paperScan({ api_key: key, api_secret: secret, config: cfg });
        setStatus(res?.signal ? `SIGNAL · price $${(res.price ?? 0).toFixed(2)} · RSI ${(res.rsi ?? 0).toFixed(1)}` : 'No signal');
      } else {
        setStatus('Manual scan via IBKR not yet supported; use Start');
      }
    } catch (e) { setStatus(`Error: ${e.message}`); }
    finally { setBusy(false); }
  };

  return (
    <div className="page">
      <div className="grid g-32" style={{ marginBottom: 14 }}>
        <Card title="Signal feed" icon="radar" actions={
          <>
            <Pill kind={s.running ? 'live' : 'off'}>{s.running ? 'SCANNING' : 'IDLE'} · {s.cadence}</Pill>
            <Btn size="sm" icon="refresh" onClick={handleScanNow} disabled={busy}>Scan now</Btn>
            <Btn variant={s.running ? 'danger' : 'primary'} size="sm" icon={s.running ? 'pause' : 'play'} disabled={busy} onClick={s.running ? handleStop : handleStart}>
              {s.running ? 'Stop' : 'Start'}
            </Btn>
          </>
        } flush>
          {status && (
            <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text-2)', background: 'var(--bg-2)', borderBottom: '1px solid var(--border-soft)' }}>
              {status}
            </div>
          )}
          <div style={{ maxHeight: 560, overflowY: 'auto' }}>
            <div className="scan-row" style={{ background: 'var(--bg-2)', fontWeight: 600, fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.6, color: 'var(--text-3)' }}>
              <span>Time</span><span></span><span>Price</span><span>RSI</span><span>Message</span><span>Filters</span>
            </div>
            {s.logs.length === 0 && (
              <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>No scans yet — press Start</div>
            )}
            {s.logs.map((l, i) => (
              <div key={i} className="scan-row">
                <span className="t">{l.t}</span>
                <span className={`dot ${l.signal ? 'hit' : ''}`} />
                <span className="mono">${(l.price ?? 0).toFixed(2)}</span>
                <span className="mono" style={{ color: l.rsi_ok ? 'var(--pos)' : 'var(--text-3)' }}>{(l.rsi ?? 0).toFixed(1)}</span>
                <span style={{ color: l.signal ? 'var(--pos)' : 'var(--text-2)', fontWeight: l.signal ? 600 : 400 }}>{l.msg}</span>
                <span style={{ display: 'flex', gap: 3 }}>
                  <Chip ok={l.rsi_ok}>RSI</Chip>
                  <Chip ok={l.ema_ok}>EMA</Chip>
                  <Chip ok={l.vol_ok}>VOL</Chip>
                </span>
              </div>
            ))}
          </div>
        </Card>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Card title="Latest signal" icon="target">
            <div style={{ textAlign: 'center', padding: '8px 0 14px' }}>
              <div style={{
                display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 16px', borderRadius: 999,
                background: s.last_signal.fire ? 'var(--pos-bg)' : 'var(--bg-2)',
                color: s.last_signal.fire ? 'var(--pos)' : 'var(--text-3)',
                fontWeight: 700, fontSize: 13, letterSpacing: 0.5,
              }}>
                <span className={s.last_signal.fire ? 'blink' : ''}>●</span>
                {s.last_signal.fire ? 'SIGNAL FIRING' : 'NO SIGNAL'}
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, fontSize: 12 }}>
              <div><div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Price</div><div className="mono" style={{ fontWeight: 600 }}>${(s.last_signal.price ?? 0).toFixed(2)}</div></div>
              <div><div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>RSI</div><div className="mono" style={{ fontWeight: 600 }}>{(s.last_signal.rsi ?? 0).toFixed(1)}</div></div>
            </div>
            <hr className="sep" />
            <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6, fontWeight: 600 }}>Filter parity</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              <Chip ok={s.last_signal.rsi_ok}>RSI&lt;{rsiThreshold}</Chip>
              <Chip ok={s.last_signal.ema_ok}>Close&lt;EMA{emaLength}</Chip>
              <Chip ok={s.last_signal.sma200_ok}>&gt;SMA200</Chip>
              <Chip ok={s.last_signal.vol_ok}>Vol&gt;MA</Chip>
              <Chip ok={s.last_signal.vix_ok}>VIX range</Chip>
              <Chip ok={s.last_signal.regime_ok}>Regime</Chip>
            </div>
          </Card>

          <Card title="Scanner config" icon="cog">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="field">
                <label>Execution mode</label>
                <select className="sel" value={mode} onChange={e => setMode(e.target.value)}>
                  <option value="paper">Paper (Alpaca)</option>
                  <option value="ibkr">IBKR live</option>
                </select>
              </div>
              <div className="field">
                <label>Cadence</label>
                <select className="sel" value={timingKey} onChange={e => setTimingKey(e.target.value)}>
                  {TIMING_OPTIONS.map(opt => (
                    <option key={`${opt.mode}:${opt.value}`} value={`${opt.mode}:${opt.value}`}>{opt.label}</option>
                  ))}
                </select>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div className="field"><label>RSI threshold</label><input className="inp" type="number" value={rsiThreshold} onChange={e => setRsiThreshold(Number(e.target.value))} /></div>
                <div className="field"><label>EMA length</label><input className="inp" type="number" value={emaLength} onChange={e => setEmaLength(Number(e.target.value))} /></div>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12 }}>
                <span>Auto-execute on signal</span><Switch on={autoExecute} onChange={setAutoExecute} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12 }}>
                <span>Persist to SQLite journal</span><Switch on={persist} onChange={setPersist} />
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
