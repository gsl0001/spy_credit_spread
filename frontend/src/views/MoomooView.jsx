import { useState, useCallback, useEffect } from 'react';
import { fmtUsd, Card, Kpi, Badge, Btn } from '../primitives.jsx';
import { api } from '../api.js';

// ── Orange design tokens (inline — no Tailwind config change needed) ──────────
const C = {
  accent:     '#f97316',   // orange-500
  accentHov:  '#fb923c',   // orange-400
  cardBg:     'rgba(124,45,18,.12)',   // orange-950/12
  cardBorder: 'rgba(154,52,18,.35)',   // orange-800/35
  pos:        '#fb923c',   // orange-400
  neg:        '#f87171',   // red-400
  warn:       '#f59e0b',
  btn:        { background: '#f97316', color: '#000', border: 'none' },
};

const OCard = ({ title, icon, subtitle, children, flush, right }) => (
  <div style={{
    background: C.cardBg, border: `1px solid ${C.cardBorder}`,
    borderRadius: 10, overflow: 'hidden',
    marginBottom: flush ? 0 : undefined,
  }}>
    {title && (
      <div style={{
        padding: '12px 16px', borderBottom: `1px solid ${C.cardBorder}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>{title}</span>
          {subtitle && <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{subtitle}</span>}
        </div>
        {right && <div>{right}</div>}
      </div>
    )}
    <div style={flush ? undefined : { padding: '14px 16px' }}>{children}</div>
  </div>
);

const OKpi = ({ label, value, sub, color }) => (
  <div style={{ background: C.cardBg, border: `1px solid ${C.cardBorder}`, borderRadius: 10, padding: '16px 20px' }}>
    <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '.04em' }}>{label}</div>
    <div style={{ fontSize: 26, fontWeight: 700, color: color || C.accent }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 4 }}>{sub}</div>}
  </div>
);

const OBtn = ({ children, onClick, disabled, small, danger }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    style={{
      background: danger ? '#dc2626' : C.accent,
      color: danger ? '#fff' : '#000',
      border: 'none', borderRadius: 6, cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? .55 : 1, fontWeight: 600,
      padding: small ? '5px 12px' : '7px 18px',
      fontSize: small ? 12 : 13,
    }}
  >
    {children}
  </button>
);

const StatusPill = ({ connected, accId }) => (
  <span style={{
    display: 'inline-flex', alignItems: 'center', gap: 6,
    background: connected ? 'rgba(249,115,22,.15)' : 'rgba(100,100,100,.15)',
    border: `1px solid ${connected ? C.cardBorder : 'rgba(100,100,100,.3)'}`,
    borderRadius: 20, padding: '3px 10px', fontSize: 12, fontWeight: 600,
    color: connected ? C.accent : 'var(--text-3)',
  }}>
    <span style={{ width: 7, height: 7, borderRadius: '50%', background: connected ? C.accent : '#6b7280', flexShrink: 0 }} />
    {connected ? `Connected · ${accId || ''}` : 'Disconnected'}
  </span>
);

// ── Main view ─────────────────────────────────────────────────────────────────

export function MoomooView() {
  // Connection state
  const [host, setHost] = useState(() => localStorage.getItem('moomoo_host') || '127.0.0.1');
  const [port, setPort] = useState(() => localStorage.getItem('moomoo_port') || '11111');
  const [tradePwd, setTradePwd] = useState(() => localStorage.getItem('moomoo_pwd') || '');
  const [trdEnv, setTrdEnv] = useState(() => Number(localStorage.getItem('moomoo_trd_env') ?? 0));
  const [secFirm, setSecFirm] = useState(() => localStorage.getItem('moomoo_sec_firm') || 'NONE');
  const [connected, setConnected] = useState(false);
  const [accId, setAccId] = useState('');
  const [connBusy, setConnBusy] = useState(false);
  const [connMsg, setConnMsg] = useState('');

  // Account
  const [account, setAccount] = useState(null);

  // Positions — journal-backed (so we have real position_id for Close)
  const [positions, setPositions] = useState([]);
  const [exitBusy, setExitBusy] = useState({});  // {position_id: bool}
  const [exitMsg, setExitMsg] = useState('');

  // Order ticket
  const [ticketDir, setTicketDir] = useState('bull_call');
  const [ticketOffset, setTicketOffset] = useState('1.50');
  const [ticketWidth, setTicketWidth] = useState('5');
  const [ticketQty, setTicketQty] = useState('1');
  const [ticketDebit, setTicketDebit] = useState('250');
  const [execBusy, setExecBusy] = useState(false);
  const [execMsg, setExecMsg] = useState('');
  const [execResult, setExecResult] = useState(null);

  // Order log
  const [orderLog, setOrderLog] = useState([]);

  // Probed account list (diagnostic)
  const [probedAccounts, setProbedAccounts] = useState(null);
  const [probeBusy, setProbeBusy] = useState(false);

  // Strategy scanner state (so user can launch ORB-on-moomoo from this view)
  const [moomooPresets, setMoomooPresets] = useState([]);
  const [selectedPreset, setSelectedPreset] = useState(() => localStorage.getItem('moomoo_preset') || 'orb-5m-moomoo');
  const [scannerActive, setScannerActive] = useState(false);
  const [scannerPreset, setScannerPreset] = useState(null);
  const [scannerBusy, setScannerBusy] = useState(false);
  const [scannerMsg, setScannerMsg] = useState('');

  const persistConn = () => {
    localStorage.setItem('moomoo_host', host);
    localStorage.setItem('moomoo_port', port);
    localStorage.setItem('moomoo_pwd', tradePwd);
    localStorage.setItem('moomoo_trd_env', String(trdEnv));
    localStorage.setItem('moomoo_sec_firm', secFirm);
  };

  const doConnect = useCallback(async () => {
    if (connBusy) return;
    persistConn();
    setConnBusy(true);
    setConnMsg('Connecting…');
    try {
      const res = await api.moomoo.connect({
        host, port: Number(port),
        trade_password: tradePwd,
        trd_env: trdEnv,
        security_firm: secFirm,
        filter_trdmarket: 'NONE',
      });
      if (res?.connected) {
        setConnected(true);
        setAccId(res.acc_id ?? '');
        setAccount(res.account ?? null);
        if (res.all_accounts) setProbedAccounts(res.all_accounts);
        setConnMsg(`✓ Connected (acc ${res.acc_id} · ${res.trd_env || ''})`);
      } else {
        setConnMsg(`✗ ${res?.error || 'connect failed'}`);
        // Auto-probe on failure to help the user diagnose
        try {
          const p = await api.moomoo.probe({ host, port: Number(port) });
          if (p?.ok) setProbedAccounts(p.accounts || []);
        } catch (_) {}
      }
    } catch (e) {
      setConnMsg(`✗ ${e.message}`);
    } finally {
      setConnBusy(false);
    }
  }, [connBusy, host, port, tradePwd, trdEnv, secFirm]);

  const doDisconnect = useCallback(async () => {
    try {
      await api.moomoo.disconnect();
    } catch (_) {}
    setConnected(false);
    setAccId('');
    setAccount(null);
    setConnMsg('Disconnected');
  }, []);

  const doFlattenAll = useCallback(async () => {
    if (!connected) {
      setExecMsg('✗ Connect to moomoo first');
      return;
    }
    if (!window.confirm('FLATTEN ALL moomoo positions? This will market-close every open spread.')) return;
    setExecBusy(true);
    setExecMsg('Flattening all moomoo positions…');
    try {
      const res = await api.moomoo.flattenAll();
      if (res?.error) {
        setExecMsg(`✗ ${res.error}`);
      } else {
        const ok = (res?.results || []).filter(r => r.ok).length;
        const total = res?.closed ?? 0;
        setExecMsg(`✓ ${ok}/${total} position(s) flattening`);
      }
      await refreshPositions();
    } catch (e) {
      setExecMsg(`✗ ${e.message}`);
    } finally {
      setExecBusy(false);
    }
  }, [connected, refreshPositions]);

  const doProbe = useCallback(async () => {
    if (probeBusy) return;
    setProbeBusy(true);
    setConnMsg('Probing OpenD…');
    try {
      const res = await api.moomoo.probe({ host, port: Number(port) });
      if (res?.ok) {
        setProbedAccounts(res.accounts || []);
        setConnMsg(`✓ Found ${res.count} account(s)`);
      } else {
        setProbedAccounts([]);
        setConnMsg(`✗ Probe failed: ${res?.error || 'unknown'}`);
      }
    } catch (e) {
      setConnMsg(`✗ Probe error: ${e.message}`);
    } finally {
      setProbeBusy(false);
    }
  }, [probeBusy, host, port]);

  // ── Strategy scanner (ORB-on-moomoo) ──────────────────────────────────────

  const loadPresets = useCallback(async () => {
    try {
      const res = await api.presetsList();
      // /api/presets returns { presets: [...] }
      const list = Array.isArray(res) ? res : (res?.presets || []);
      const moomooOnly = list.filter(p => (p?.broker || 'ibkr') === 'moomoo');
      setMoomooPresets(moomooOnly);
      // If selected preset isn't in the list, fall back to first
      if (moomooOnly.length && !moomooOnly.find(p => p.name === selectedPreset)) {
        setSelectedPreset(moomooOnly[0].name);
      }
    } catch (_) {}
  }, [selectedPreset]);

  const refreshScannerStatus = useCallback(async () => {
    try {
      const s = await api.presetScannerStatus();
      setScannerActive(!!s?.active);
      setScannerPreset(s?.preset || null);
    } catch (_) {}
  }, []);

  const doScannerStart = useCallback(async () => {
    if (scannerBusy || !selectedPreset) return;
    setScannerBusy(true);
    setScannerMsg('Starting scanner…');
    try {
      const res = await api.presetScannerStart(selectedPreset);
      if (res?.error) {
        setScannerMsg(`✗ ${res.error}: ${res.detail || ''}`);
      } else {
        setScannerActive(true);
        setScannerPreset(res.preset);
        localStorage.setItem('moomoo_preset', selectedPreset);
        setScannerMsg(`✓ Scanning '${selectedPreset}' every ${res.preset?.timing_value}${res.preset?.timing_mode === 'interval' ? 's' : ' (cron)'}`);
      }
    } catch (e) {
      setScannerMsg(`✗ ${e.message}`);
    } finally {
      setScannerBusy(false);
    }
  }, [scannerBusy, selectedPreset]);

  const doScannerStop = useCallback(async () => {
    if (scannerBusy) return;
    setScannerBusy(true);
    try {
      await api.presetScannerStop();
      setScannerActive(false);
      setScannerPreset(null);
      setScannerMsg('Stopped');
    } catch (e) {
      setScannerMsg(`✗ ${e.message}`);
    } finally {
      setScannerBusy(false);
    }
  }, [scannerBusy]);

  const doScannerTickNow = useCallback(async () => {
    if (scannerBusy) return;
    setScannerBusy(true);
    setScannerMsg('Running one tick…');
    try {
      const res = await api.presetScannerTick();
      if (res?.error) {
        setScannerMsg(`✗ ${res.error}: ${res.detail || ''}`);
      } else {
        const fired = (res.signals || []).filter(s => s.fired).length;
        setScannerMsg(`✓ Tick complete · ${res.signals?.length || 0} signals · ${fired} fired`);
      }
    } catch (e) {
      setScannerMsg(`✗ ${e.message}`);
    } finally {
      setScannerBusy(false);
    }
  }, [scannerBusy]);

  const refreshAccount = useCallback(async () => {
    if (!connected) return;
    try {
      const res = await api.moomoo.account();
      if (!res?.error) setAccount(res);
    } catch (_) {}
  }, [connected]);

  const refreshPositions = useCallback(async () => {
    // Pull from journal so we have position_id + legs for Close.
    // Filter to moomoo so we don't show IBKR positions in the moomoo view.
    try {
      const res = await api.openPositions();
      const all = res?.positions || [];
      setPositions(all.filter(p => (p.broker || 'ibkr') === 'moomoo'));
    } catch (_) {}
  }, []);

  useEffect(() => {
    // Always poll journal positions (so user sees what was opened even if
    // they reload before reconnecting).  Account refresh requires connect.
    refreshPositions();
    if (!connected) return;
    refreshAccount();
    const id = setInterval(() => { refreshAccount(); refreshPositions(); }, 15000);
    return () => clearInterval(id);
  }, [connected, refreshAccount, refreshPositions]);

  // Load presets list + scanner status on mount; refresh status every 10s
  useEffect(() => {
    loadPresets();
    refreshScannerStatus();
    const id = setInterval(refreshScannerStatus, 10000);
    return () => clearInterval(id);
  }, [loadPresets, refreshScannerStatus]);

  const doExecute = useCallback(async () => {
    if (execBusy) return;
    if (!connected) {
      setExecMsg('✗ Connect to moomoo first');
      return;
    }
    const qty = Number(ticketQty);
    const debit = Number(ticketDebit);
    const width = Number(ticketWidth);
    const offset = Number(ticketOffset);
    if (!Number.isFinite(qty) || qty <= 0) { setExecMsg('✗ Qty must be > 0'); return; }
    if (!Number.isFinite(debit) || debit <= 0) { setExecMsg('✗ Spread cost target must be > 0'); return; }
    if (!Number.isFinite(width) || width <= 0) { setExecMsg('✗ Strike width must be > 0'); return; }
    if (!Number.isFinite(offset)) { setExecMsg('✗ Offset must be a number'); return; }

    setExecBusy(true);
    setExecMsg('Placing order…');
    setExecResult(null);
    try {
      const res = await api.moomoo.execute({
        host, port: Number(port), trade_password: tradePwd,
        direction: ticketDir,
        contracts: qty,
        strike_width: width,
        target_dte: 0,
        spread_cost_target: debit,
        otm_offset: offset,
      });
      if (res?.success) {
        setExecMsg(`✓ Placed · K_long ${res.K_long} / K_short ${res.K_short} · ${res.contracts}c`);
        setExecResult(res);
        setOrderLog(prev => [{ time: new Date().toLocaleTimeString(), ...res, status: 'filled' }, ...prev.slice(0, 19)]);
        await refreshPositions();
      } else {
        // Server returned a structured error (HTTP 200 + {error, reason})
        const err = res?.error || 'order rejected';
        const why = res?.reason ? ` — ${res.reason}` : '';
        setExecMsg(`✗ ${err}${why}`);
      }
    } catch (e) {
      // Distinguish fetch-level failures from server-side errors
      const m = e?.message || '';
      let pretty;
      if (e?.name === 'TimeoutError' || /aborted|timed out|timeout/i.test(m)) {
        pretty = 'Request timed out (>90s). Leg fills may still be in flight — check Positions.';
      } else if (e?.name === 'TypeError' || /load failed|failed to fetch|network/i.test(m)) {
        pretty = 'Network error — backend unreachable or restarted mid-request. Try again.';
      } else if (/→ 5\d\d/.test(m)) {
        pretty = `Server error (${m.match(/→ (\d+)/)?.[1] || '5xx'}). Check the FastAPI logs.`;
      } else {
        pretty = m || 'unknown error';
      }
      setExecMsg(`✗ ${pretty}`);
    } finally {
      setExecBusy(false);
      // Always refresh positions so any partial fill is visible.
      refreshPositions();
    }
  }, [execBusy, connected, host, port, tradePwd, ticketDir, ticketOffset, ticketWidth, ticketQty, ticketDebit, refreshPositions]);

  const doExit = useCallback(async (posId) => {
    if (!posId) {
      setExitMsg('✗ no position_id');
      return;
    }
    if (!connected) {
      setExitMsg('✗ moomoo not connected — connect first');
      return;
    }
    setExitBusy(b => ({ ...b, [posId]: true }));
    setExitMsg(`Closing ${posId.slice(0, 12)}…`);
    try {
      const res = await api.moomoo.exit({ position_id: posId });
      if (res?.error) {
        setExitMsg(`✗ ${res.error}`);
      } else {
        const n = (res?.close_orders || []).length;
        setExitMsg(`✓ Closed ${posId.slice(0, 12)} · ${n} leg order(s) sent`);
      }
      await refreshPositions();
    } catch (e) {
      setExitMsg(`✗ ${e.message}`);
    } finally {
      setExitBusy(b => ({ ...b, [posId]: false }));
    }
  }, [connected, refreshPositions]);

  const inp = (val, set, type = 'text') => ({
    type, value: val, onChange: e => set(e.target.value),
    style: {
      background: 'var(--bg-2)', border: '1px solid var(--border)',
      borderRadius: 6, padding: '6px 10px', color: 'var(--text-1)',
      fontSize: 13, width: '100%', boxSizing: 'border-box',
    }
  });

  return (
    <div className="page">

      {/* ── KPI row ── */}
      <div className="grid g-4" style={{ marginBottom: 14 }}>
        <OKpi label="Total Assets" value={fmtUsd(account?.equity ?? 0)} sub="moomoo account" />
        <OKpi label="Buying Power" value={fmtUsd(account?.buying_power ?? 0)} sub="available" />
        <OKpi label="Unrealized P&L" value={fmtUsd(account?.unrealized_pnl ?? 0, true)}
          color={(account?.unrealized_pnl ?? 0) >= 0 ? C.pos : C.neg} />
        <OKpi label="Realized P&L" value={fmtUsd(account?.realized_pnl ?? 0, true)}
          color={(account?.realized_pnl ?? 0) >= 0 ? C.pos : C.neg} />
      </div>

      <div className="grid g-23" style={{ gap: 14 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* ── Connection panel ── */}
          <OCard title="moomoo OpenD Connection" subtitle="Requires OpenD v8.3+ running locally">
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12 }}>
              <div style={{ flex: '1 1 120px' }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Host</div>
                <input {...inp(host, setHost)} />
              </div>
              <div style={{ flex: '0 0 80px' }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Port</div>
                <input {...inp(port, setPort, 'number')} />
              </div>
              <div style={{ flex: '1 1 140px' }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Trade Password</div>
                <input {...inp(tradePwd, setTradePwd, 'password')} placeholder="PIN (real only)" />
              </div>
              <div style={{ flexShrink: 0 }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Mode</div>
                <div style={{ display: 'flex', gap: 4 }}>
                  {[{ label: 'Simulate', val: 0 }, { label: 'Real', val: 1 }].map(({ label, val }) => (
                    <button key={val} disabled={connected} onClick={() => setTrdEnv(val)} style={{
                      padding: '5px 10px', fontSize: 12, borderRadius: 6, cursor: connected ? 'default' : 'pointer',
                      border: `1px solid ${trdEnv === val ? C.accent : 'rgba(100,100,100,.3)'}`,
                      background: trdEnv === val ? 'rgba(249,115,22,.15)' : 'transparent',
                      color: trdEnv === val ? C.accent : 'var(--text-3)',
                    }}>{label}</button>
                  ))}
                </div>
              </div>
              <div style={{ flexShrink: 0 }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Firm</div>
                <select disabled={connected} value={secFirm} onChange={e => setSecFirm(e.target.value)} style={{
                  padding: '5px 8px', fontSize: 12, borderRadius: 6, border: '1px solid rgba(100,100,100,.3)',
                  background: 'var(--bg-card)', color: 'var(--text-1)', cursor: connected ? 'default' : 'pointer',
                }}>
                  <option value="NONE">Auto (NONE)</option>
                  <option value="FUTUINC">Futu Inc (US)</option>
                  <option value="FUTUSECURITIES">Futu Securities (HK)</option>
                  <option value="FUTUSG">Futu SG</option>
                  <option value="FUTUCA">Futu CA</option>
                  <option value="FUTUAU">Futu AU</option>
                </select>
              </div>
              <OBtn onClick={connected ? doDisconnect : doConnect} disabled={connBusy} danger={connected}>
                {connBusy ? '…' : connected ? 'Disconnect' : 'Connect'}
              </OBtn>
              <OBtn onClick={doProbe} disabled={probeBusy || connected}>
                {probeBusy ? '…' : 'Probe'}
              </OBtn>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <StatusPill connected={connected} accId={accId} />
              {connMsg && (
                <span style={{ fontSize: 12, color: connMsg.startsWith('✓') ? C.pos : connMsg.startsWith('✗') ? C.neg : 'var(--text-3)' }}>
                  {connMsg}
                </span>
              )}
            </div>

            {/* Account picker — populated by Probe or auto-fill on connect failure */}
            {Array.isArray(probedAccounts) && probedAccounts.length > 0 && (
              <div style={{
                marginTop: 12,
                background: 'rgba(0,0,0,.18)', border: '1px solid rgba(255,255,255,.06)',
                borderRadius: 8, padding: '10px 12px',
              }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.4 }}>
                  Available accounts in OpenD ({probedAccounts.length})
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={{ color: 'var(--text-3)', textAlign: 'left', borderBottom: '1px solid rgba(255,255,255,.08)' }}>
                        <th style={{ padding: '4px 8px' }}>acc_id</th>
                        <th style={{ padding: '4px 8px' }}>Mode</th>
                        <th style={{ padding: '4px 8px' }}>Firm</th>
                        <th style={{ padding: '4px 8px' }}>Markets</th>
                        <th style={{ padding: '4px 8px' }}>Type</th>
                        <th style={{ padding: '4px 8px' }}>Status</th>
                        <th style={{ padding: '4px 8px' }}></th>
                      </tr>
                    </thead>
                    <tbody>
                      {probedAccounts.map((a, i) => {
                        const auth = Array.isArray(a.trdmarket_auth) ? a.trdmarket_auth.join(',') : String(a.trdmarket_auth || '');
                        const isReal = a.trd_env === 'REAL';
                        const firm = a.security_firm && a.security_firm !== 'N/A' ? a.security_firm : '—';
                        return (
                          <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,.04)' }}>
                            <td style={{ padding: '4px 8px', fontFamily: 'monospace' }}>{String(a.acc_id)}</td>
                            <td style={{ padding: '4px 8px', color: isReal ? '#fb923c' : '#94a3b8' }}>{a.trd_env}</td>
                            <td style={{ padding: '4px 8px' }}>{firm}</td>
                            <td style={{ padding: '4px 8px' }}>{auth}</td>
                            <td style={{ padding: '4px 8px' }}>{a.acc_type}</td>
                            <td style={{ padding: '4px 8px' }}>{a.acc_status}</td>
                            <td style={{ padding: '4px 8px' }}>
                              {!connected && (
                                <button onClick={() => {
                                  setTrdEnv(isReal ? 1 : 0);
                                  setSecFirm(firm !== '—' ? firm : 'NONE');
                                  setConnMsg(`Selected ${a.trd_env} / ${firm} — click Connect`);
                                }} style={{
                                  fontSize: 11, padding: '3px 8px', borderRadius: 4,
                                  border: `1px solid ${C.cardBorder}`,
                                  background: 'rgba(249,115,22,.1)', color: C.accent, cursor: 'pointer',
                                }}>Use</button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </OCard>

          {/* ── Strategy Scanner (auto-trading) ── */}
          <OCard title="Strategy Scanner" subtitle="ORB-on-moomoo · auto-execute on signal">
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 10 }}>
              <div style={{ flex: '1 1 200px', minWidth: 200 }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Preset</div>
                <select
                  value={selectedPreset}
                  onChange={e => setSelectedPreset(e.target.value)}
                  disabled={scannerActive || scannerBusy}
                  style={{
                    width: '100%', padding: '6px 8px', fontSize: 13, borderRadius: 6,
                    border: '1px solid rgba(100,100,100,.3)',
                    background: 'var(--bg-card)', color: 'var(--text-1)',
                  }}
                >
                  {moomooPresets.length === 0 && <option value="">(no moomoo presets)</option>}
                  {moomooPresets.map(p => (
                    <option key={p.name} value={p.name}>
                      {p.name} · {p.strategy_name} · {p.timing_mode}={p.timing_value}
                    </option>
                  ))}
                </select>
              </div>
              {!scannerActive ? (
                <OBtn onClick={doScannerStart} disabled={!connected || scannerBusy || !selectedPreset}>
                  {scannerBusy ? '…' : 'Start'}
                </OBtn>
              ) : (
                <OBtn onClick={doScannerStop} disabled={scannerBusy} danger>
                  {scannerBusy ? '…' : 'Stop'}
                </OBtn>
              )}
              <OBtn onClick={doScannerTickNow} disabled={!scannerActive || scannerBusy}>
                Tick Now
              </OBtn>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12, flexWrap: 'wrap' }}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                background: scannerActive ? 'rgba(34,197,94,.15)' : 'rgba(100,100,100,.15)',
                border: `1px solid ${scannerActive ? 'rgba(34,197,94,.4)' : 'rgba(100,100,100,.3)'}`,
                borderRadius: 20, padding: '3px 10px', fontWeight: 600,
                color: scannerActive ? '#22c55e' : 'var(--text-3)',
              }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: scannerActive ? '#22c55e' : '#6b7280' }} />
                {scannerActive ? `Scanning ${scannerPreset?.name || ''}` : 'Scanner idle'}
              </span>
              {scannerMsg && (
                <span style={{ color: scannerMsg.startsWith('✓') ? C.pos : scannerMsg.startsWith('✗') ? C.neg : 'var(--text-3)' }}>
                  {scannerMsg}
                </span>
              )}
              {!connected && (
                <span style={{ color: C.neg }}>Connect to moomoo first.</span>
              )}
            </div>
            <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-3)', lineHeight: 1.5 }}>
              ORB strategy trades only on Mon/Wed/Fri when VIX is 15-25, the OR range
              ≥ 0.05% of price, and the day is not a scheduled high-impact news day
              (FOMC, NFP, CPI). Today (2026-04-29) is FOMC and will be skipped.
            </div>
          </OCard>

          {/* ── Legged execution warning ── */}
          <div style={{
            background: 'rgba(249,115,22,.1)', border: `1px solid ${C.cardBorder}`,
            borderRadius: 8, padding: '10px 14px', fontSize: 12,
            color: C.accentHov, display: 'flex', gap: 8, alignItems: 'flex-start',
          }}>
            <span style={{ fontSize: 16, marginTop: -1 }}>⚠</span>
            <span>
              <strong>Legged execution:</strong> Spreads place 2 sequential single-leg orders.
              If leg 2 fails or times out, leg 1 is automatically flattened at market price.
              Monitor order log below for status.
            </span>
          </div>

          {/* ── Order ticket ── */}
          <OCard title="Order Ticket" subtitle="SPY 0DTE · legged spread">
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
              {/* Direction toggle */}
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Direction</div>
                <div style={{ display: 'flex', gap: 0, border: `1px solid ${C.cardBorder}`, borderRadius: 6, overflow: 'hidden' }}>
                  {['bull_call', 'bear_put'].map(d => (
                    <button key={d} onClick={() => setTicketDir(d)} style={{
                      background: ticketDir === d ? C.accent : 'transparent',
                      color: ticketDir === d ? '#000' : 'var(--text-2)',
                      border: 'none', padding: '6px 14px', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                    }}>
                      {d === 'bull_call' ? 'Bull Call' : 'Bear Put'}
                    </button>
                  ))}
                </div>
              </div>
              <div style={{ flex: '0 0 80px' }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Offset (pts)</div>
                <input {...inp(ticketOffset, setTicketOffset, 'number')} />
              </div>
              <div style={{ flex: '0 0 60px' }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Width ($)</div>
                <input {...inp(ticketWidth, setTicketWidth, 'number')} />
              </div>
              <div style={{ flex: '0 0 60px' }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Qty</div>
                <input {...inp(ticketQty, setTicketQty, 'number')} />
              </div>
              <div style={{ flex: '0 0 90px' }}>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 4 }}>Net Debit ($)</div>
                <input {...inp(ticketDebit, setTicketDebit, 'number')} />
              </div>
              <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                <OBtn onClick={doExecute} disabled={execBusy || !connected}>
                  {execBusy ? 'Placing…' : 'Execute'}
                </OBtn>
              </div>
            </div>
            {execMsg && (
              <div style={{ fontSize: 12, color: execMsg.startsWith('✓') ? C.pos : execMsg.startsWith('✗') ? C.neg : 'var(--text-3)', marginBottom: 8 }}>
                {execMsg}
              </div>
            )}
            {execResult && (
              <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--text-2)' }}>
                <span>K_long <strong style={{ color: C.accent }}>{execResult.K_long}</strong></span>
                <span>K_short <strong style={{ color: C.accent }}>{execResult.K_short}</strong></span>
                <span>Debit <strong>{fmtUsd(execResult.debit_per_contract * 100)}</strong></span>
                <span>Leg1 <code style={{ fontSize: 11 }}>{execResult.leg1_order_id}</code></span>
                <span>Leg2 <code style={{ fontSize: 11 }}>{execResult.leg2_order_id}</code></span>
              </div>
            )}
          </OCard>

        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* ── Positions table (journal-backed) ── */}
          <OCard
            title="Positions"
            subtitle={`moomoo · ${positions.length} open spread${positions.length === 1 ? '' : 's'}`}
            flush
            right={positions.length > 0 ? (
              <OBtn small danger disabled={!connected || execBusy} onClick={doFlattenAll}>
                Flatten All
              </OBtn>
            ) : null}
          >
            <table className="tbl">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Symbol</th>
                  <th>Direction</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry $</th>
                  <th>Legs</th>
                  <th>Expiry</th>
                  <th>State</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {positions.length === 0 && (
                  <tr><td colSpan="9" style={{ textAlign: 'center', padding: 24, color: 'var(--text-3)' }}>No moomoo positions</td></tr>
                )}
                {positions.map(p => {
                  const dirLabel = p.direction === 'bull' ? 'BULL' : p.direction === 'bear' ? 'BEAR' : (p.direction || '—').toUpperCase();
                  const dirVar = p.direction === 'bull' ? 'pos' : p.direction === 'bear' ? 'neg' : 'default';
                  const legs = Array.isArray(p.legs) ? p.legs : [];
                  const legSummary = legs.length === 0 ? '—'
                    : legs.map(l => {
                        const r = (l.right || '').toUpperCase();
                        const side = l.side === 'long' ? '+' : l.side === 'short' ? '−' : '';
                        return `${side}${l.strike}${r}`;
                      }).join(' / ');
                  const busy = !!exitBusy[p.id];
                  return (
                    <tr key={p.id}>
                      <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{(p.id || '').slice(0, 12)}</td>
                      <td>{p.symbol ?? '—'}</td>
                      <td><Badge variant={dirVar} dot>{dirLabel}</Badge></td>
                      <td className="num">{Number(p.contracts ?? 0)}</td>
                      <td className="num">{fmtUsd(p.entry_cost ?? 0)}</td>
                      <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{legSummary}</td>
                      <td style={{ fontSize: 11 }}>{p.expiry ?? '—'}</td>
                      <td><Badge variant={p.state === 'open' ? 'pos' : 'default'}>{p.state ?? '—'}</Badge></td>
                      <td>
                        <OBtn small danger disabled={busy || !connected} onClick={() => doExit(p.id)}>
                          {busy ? '…' : 'Close'}
                        </OBtn>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {(exitMsg || !connected) && (
              <div style={{
                padding: '8px 12px', borderTop: '1px solid rgba(255,255,255,.06)', fontSize: 12,
                color: exitMsg.startsWith('✓') ? C.pos : exitMsg.startsWith('✗') ? C.neg : 'var(--text-3)',
              }}>
                {!connected
                  ? 'Connect to moomoo to enable Close.'
                  : exitMsg}
              </div>
            )}
          </OCard>

          {/* ── Order log ── */}
          <OCard title="Order Log" subtitle="legged execution results">
            {orderLog.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--text-3)', textAlign: 'center', padding: '20px 0' }}>No orders this session</div>
            ) : (
              <table className="tbl" style={{ fontSize: 12 }}>
                <thead>
                  <tr><th>Time</th><th>K_long</th><th>K_short</th><th>Qty</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {orderLog.map((o, i) => (
                    <tr key={i}>
                      <td style={{ color: 'var(--text-3)' }}>{o.time}</td>
                      <td className="num">{o.K_long ?? '—'}</td>
                      <td className="num">{o.K_short ?? '—'}</td>
                      <td className="num">{o.contracts ?? 1}</td>
                      <td>
                        <Badge variant={o.status === 'filled' ? 'pos' : 'neg'} dot>{o.status}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </OCard>

        </div>
      </div>
    </div>
  );
}
