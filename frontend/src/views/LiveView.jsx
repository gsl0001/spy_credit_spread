import { useState, useEffect, useCallback, useRef } from 'react';
import { useData } from '../useBackendData.jsx';
import { Ico } from '../icons.jsx';
import { fmtUsd, fmtTimeAgo, Card, Kpi, Badge, Btn, Pill, Heartbeat, Chip } from '../primitives.jsx';
import { api, safe, IBKR_CREDS, setIbkrCreds } from '../api.js';
import { loadConfig } from '../backtestConfig.js';

export function LiveView() {
  const m = useData();
  const [showTicket, setShowTicket] = useState(false);
  const [host, setHost] = useState(IBKR_CREDS.host);
  const [port, setPort] = useState(IBKR_CREDS.port);
  const [clientId, setClientId] = useState(IBKR_CREDS.client_id);
  const [ibkrAccount, setIbkrAccount] = useState(null);
  const [ibkrPositions, setIbkrPositions] = useState([]);
  const [journalPositions, setJournalPositions] = useState([]);
  const [connectMsg, setConnectMsg] = useState('');
  const [ticketMsg, setTicketMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [targetDte, setTargetDte] = useState(loadConfig().target_dte);
  const [contractsOverride, setContractsOverride] = useState(0);
  const [chainPreview, setChainPreview] = useState(null);
  const [chainBusy, setChainBusy] = useState(false);

  // Preset scanner state
  const [presetList, setPresetList] = useState([]);
  const [activePreset, setActivePreset] = useState('');
  const [scannerRunning, setScannerRunning] = useState(false);
  const [scannerMsg, setScannerMsg] = useState('');
  const [logClearedAt, setLogClearedAt] = useState(null);  // timestamp to filter old logs
  const [showPresetInfo, setShowPresetInfo] = useState(false);

  // Telegram bot state
  const [tgStatus, setTgStatus] = useState(null);
  const [tgBusy, setTgBusy] = useState(false);
  const [tgMsg, setTgMsg] = useState('');
  const [tgCustom, setTgCustom] = useState('');

  // Local ack tracker — alerts older than this timestamp are visually
  // "read". The backend doesn't persist read state, so this resets when
  // the view unmounts; that matches the typical alert-tray UX.
  const [alertsAckedAt, setAlertsAckedAt] = useState(null);

  const monitorAge = m.monitor.last_tick_iso
    ? Math.floor((new Date() - new Date(m.monitor.last_tick_iso)) / 1000)
    : null;
  const tickState = monitorAge === null ? 'fail' : monitorAge < 30 ? 'ok' : monitorAge < 60 ? 'stale' : 'fail';

  const connect = useCallback(async () => {
    setBusy(true); setConnectMsg('Connecting…');
    setIbkrCreds({ host, port, client_id: clientId });
    try {
      const res = await api.ibkrConnect();
      if (res?.connected) {
        setIbkrAccount(res.summary);
        setConnectMsg(`Connected · ${res.summary?.account_id || ''}`);
        const [p, j] = await Promise.all([safe(api.ibkrPositions), safe(api.openPositions)]);
        if (p?.positions) setIbkrPositions(p.positions);
        if (j?.positions) setJournalPositions(j.positions);
      } else {
        setConnectMsg(res?.error || 'Connect failed');
      }
    } catch (e) { setConnectMsg(`Error: ${e.message}`); }
    finally { setBusy(false); }
  }, [host, port, clientId]);

  const disconnect = useCallback(() => {
    setIbkrAccount(null);
    setIbkrPositions([]);
    setJournalPositions([]);
    setConnectMsg('Disconnected');
  }, []);

  // Track ibkrAccount via ref so the polling closure reads the current
  // value without forcing the effect to re-run on every state change
  // (which would otherwise tear down and re-create the interval, kicking
  // off another async cascade — a thrash loop).
  const ibkrAccountRef = useRef(ibkrAccount);
  useEffect(() => { ibkrAccountRef.current = ibkrAccount; }, [ibkrAccount]);

  useEffect(() => {
    let cancelled = false;
    // 1. Initial check on mount.
    (async () => {
      const hb = await safe(api.heartbeat);
      if (cancelled || !hb?.alive) return;
      const [a, p, j] = await Promise.all([
        safe(api.ibkrConnect),
        safe(api.ibkrPositions),
        safe(api.openPositions),
      ]);
      if (cancelled) return;
      if (a?.connected) setIbkrAccount(a.summary);
      if (p?.positions) setIbkrPositions(p.positions);
      if (j?.positions) setJournalPositions(j.positions);
      setConnectMsg(`Connected · ${a?.summary?.account_id || ''}`);
    })();

    // 2. Periodic poll. Reads ibkrAccountRef so we react to a manual
    //    disconnect without rebinding the interval each render.
    const poll = async () => {
      const hb = await safe(api.heartbeat);
      if (cancelled) return;
      if (!hb?.alive) {
        if (ibkrAccountRef.current) {
          setIbkrAccount(null);
          setConnectMsg('Connection lost');
        }
        return;
      }
      const [a, p, j] = await Promise.all([
        safe(api.ibkrConnect),
        safe(api.ibkrPositions),
        safe(api.openPositions),
      ]);
      if (cancelled) return;
      if (a?.connected) {
        setIbkrAccount(a.summary);
        setConnectMsg(`Connected · ${a.summary?.account_id || ''}`);
      }
      if (p?.positions) setIbkrPositions(p.positions);
      if (j?.positions) setJournalPositions(j.positions);
    };

    const id = setInterval(poll, 30000);
    return () => { cancelled = true; clearInterval(id); };
  }, []); // mount-once — see ref pattern above

  const exitPosition = useCallback(async (posId) => {
    setBusy(true);
    setTicketMsg(`Closing position ${posId.slice(0,8)}…`);
    try {
      const res = await api.ibkrExit(posId);
      if (res?.error) setTicketMsg(`Exit failed: ${res.error}`);
      else setTicketMsg(`Exit order submitted · ${res.order_id || ''}`);
    } catch (e) { setTicketMsg(`Exit error: ${e.message}`); }
    finally { setBusy(false); }
  }, []);

  const refreshPresets = useCallback(async () => {
    const r = await safe(api.presetsList, {});
    setPresetList(Array.isArray(r?.presets) ? r.presets : []);
  }, []);

  useEffect(() => {
    refreshPresets();
  }, [refreshPresets]);

  const submitOrder = useCallback(async () => {
    setBusy(true); setTicketMsg('Submitting order…');
    try {
      const cfg = loadConfig();
      // I5: Generate unique ID on the client to prevent duplicate submissions
      const clientOrderId = crypto.randomUUID();
      
      const payload = {
        symbol: cfg.ticker,
        topology: cfg.topology,
        direction: cfg.strategy_type === 'bear_put' ? 'bear_put' : 'bull_call',
        contracts: Number(contractsOverride) || 0,
        strike_width: cfg.strike_width,
        target_dte: Number(targetDte) || cfg.target_dte,
        spread_cost_target: cfg.spread_cost_target,
        // Sizing — propagate from saved config so manual orders honour
        // the same dynamic-sizing toggle as the backtester / auto-execute.
        // Without these, sizing_mode_from_request() defaults to "fixed"
        // regardless of what the user configured.
        use_dynamic_sizing: !!cfg.use_dynamic_sizing,
        risk_percent: Number(cfg.risk_percent) || 5.0,
        max_trade_cap: Number(cfg.max_trade_cap) || 0,
        stop_loss_pct: cfg.stop_loss_pct,
        take_profit_pct: cfg.take_profit_pct,
        trailing_stop_pct: cfg.trailing_stop_pct,
        client_order_id: clientOrderId,
      };
      const res = await api.ibkrExecute(payload);
      if (res?.error) setTicketMsg(`Error: ${res.error}`);
      else { 
        const status = res.duplicate ? ' (duplicate suppressed)' : '';
        setTicketMsg(`Submitted ${res.contracts || ''} contracts · order ${res.order_id || ''}${status}`); 
        setShowTicket(true); 
      }
    } catch (e) { setTicketMsg(`Error: ${e.message}`); }
    finally { setBusy(false); }
  }, [targetDte, contractsOverride]);

  const previewChain = useCallback(async () => {
    setChainBusy(true);
    try {
      const cfg = loadConfig();
      const res = await api.liveChain(cfg.ticker || 'SPY');
      const dte = Number(targetDte) || cfg.target_dte;
      const target = new Date(Date.now() + dte * 86400000);
      const chains = res?.chains || [];
      const pick = chains.reduce((best, c) => {
        const diff = Math.abs(new Date(c.expiration) - target);
        return !best || diff < best._d ? { ...c, _d: diff } : best;
      }, null);
      const px = res?.price || 0;
      const width = cfg.strike_width || 5;
      const legs = pick?.calls?.filter(o => Math.abs(o.strike - px) <= width * 1.5).slice(0, 4) || [];
      setChainPreview({ price: px, expiration: pick?.expiration, legs });
    } catch (e) { setChainPreview({ error: e.message }); }
    finally { setChainBusy(false); }
  }, [targetDte]);

  const flattenAll = useCallback(async () => {
    setBusy(true); setTicketMsg('Flattening all…');
    try {
      const res = await api.flatten();
      setTicketMsg(res?.error ? `Error: ${res.error}` : `Flatten submitted (${res?.closed || 0})`);
    } catch (e) { setTicketMsg(`Error: ${e.message}`); }
    finally { setBusy(false); }
  }, []);

  const startPreset = useCallback(async () => {
    if (!activePreset) return;
    setScannerMsg('Starting…');
    try {
      const r = await api.presetScannerStart(activePreset);
      setScannerRunning(!r?.error);
      setScannerMsg(r?.error || `Scanning · ${activePreset}`);
    } catch (e) { setScannerMsg(e.message); }
  }, [activePreset]);

  const stopPreset = useCallback(async () => {
    try { await api.presetScannerStop(); } catch (_) {}
    setScannerRunning(false);
    setScannerMsg('Stopped');
  }, []);

  // Telegram status — refresh on mount + every 30s while view is open.
  useEffect(() => {
    let mounted = true;
    const load = async () => {
      const r = await safe(api.telegramStatus, null);
      if (mounted) setTgStatus(r);
    };
    load();
    const id = setInterval(load, 30000);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  const sendTgTest = useCallback(async () => {
    setTgBusy(true); setTgMsg('Sending…');
    try {
      const r = await api.telegramTest();
      setTgMsg(r?.sent ? '✓ Test message sent' : `✗ ${r?.error || r?.detail || 'failed'}`);
    } catch (e) {
      setTgMsg(`✗ ${e.message}`);
    } finally {
      setTgBusy(false);
      setTimeout(() => setTgMsg(''), 4000);
    }
  }, []);

  const sendTgCustom = useCallback(async () => {
    const text = tgCustom.trim();
    if (!text) return;
    setTgBusy(true); setTgMsg('Sending…');
    try {
      const r = await api.telegramTest(text);
      if (r?.sent) {
        setTgMsg('✓ Sent');
        setTgCustom('');
      } else {
        setTgMsg(`✗ ${r?.error || 'failed'}`);
      }
    } catch (e) {
      setTgMsg(`✗ ${e.message}`);
    } finally {
      setTgBusy(false);
      setTimeout(() => setTgMsg(''), 4000);
    }
  }, [tgCustom]);

  const toggleScanner = useCallback(() => {
    if (scannerRunning) stopPreset();
    else startPreset();
  }, [scannerRunning, startPreset, stopPreset]);

  // Only show financial figures that came from IBKR — show '—' from MOCK zeros
  const connected = !!ibkrAccount;
  const acct = ibkrAccount || {};
  // Re-read config each render so backtester edits flow through
  const cfg = loadConfig();

  return (
    <div className="page">

      {/* ── Row 1: Account Info | IBKR Connection ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1.2fr', gap: 10, marginBottom: 10 }}>

        {/* Account Info */}
        <Card title="Account Info" icon="dashboard"
              subtitle={connected ? `Account ${acct.account_id || ''}` : 'connect IBKR to load'}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, height: '100%', placeItems: 'center' }}>
            <Kpi label="Equity"
                 value={connected ? fmtUsd(acct.equity) : '—'} big />
            <Kpi label="Day P&L"
                 value={connected ? fmtUsd(acct.daily_pnl ?? m.account.daily_pnl, true) : (m.account.daily_pnl ? fmtUsd(m.account.daily_pnl, true) : '—')}
                 color={connected || m.account.daily_pnl ? ((acct.daily_pnl ?? m.account.daily_pnl) >= 0 ? 'var(--pos)' : 'var(--neg)') : undefined} big />
            <Kpi label="Unrealized"
                 value={connected ? fmtUsd(acct.unrealized_pnl ?? 0, true) : '—'}
                 color={connected ? ((acct.unrealized_pnl ?? 0) >= 0 ? 'var(--pos)' : 'var(--neg)') : undefined} big />
            <Kpi label="Buying Power"
                 value={connected ? fmtUsd(acct.buying_power ?? 0) : '—'} big />
          </div>
        </Card>

        {/* IBKR Connection */}
        <Card title="IBKR Connection" icon="radar"
              subtitle={connectMsg || (connected ? 'connected' : 'not connected')}
              actions={
                <Pill kind={connected ? 'live' : m.__ibkr === 'warn' ? 'warn' : 'off'}>
                  {connected ? 'CONNECTED' : (m.__ibkr||'off').toUpperCase()}
                </Pill>
              }>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, height: '100%', justifyContent: 'center' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
              <div className="field"><label>Host</label><input className="inp" value={host} onChange={e=>setHost(e.target.value)} /></div>
              <div className="field"><label>Port</label><input className="inp" type="number" value={port} onChange={e=>setPort(Number(e.target.value))} /></div>
              <div className="field"><label>Client ID</label><input className="inp" type="number" value={clientId} onChange={e=>setClientId(Number(e.target.value))} /></div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Btn variant={connected ? "danger" : "primary"} 
                   icon={connected ? "power" : "activity"} 
                   disabled={busy} 
                   onClick={connected ? disconnect : connect} 
                   style={{flex:1, justifyContent: 'center', height: 38}}>
                {connected ? 'Disconnect' : 'Connect'}
              </Btn>
              <button className="panic-btn" 
                      disabled={busy||!connected} 
                      onClick={flattenAll} 
                      style={{flex:1, justifyContent: 'center', height: 38}}>
                <Ico name="zap" size={12} /> KILL SWITCH
              </button>
            </div>
          </div>
        </Card>
      </div>

      {/* ── Row 2: Monitor Heartbeat | Alerts ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 10, marginBottom: 10 }}>

        {/* Monitor Heartbeat */}
        <Card title="Monitor Heartbeat" icon="activity" style={{ minWidth: 200 }}
              actions={<Pill kind={tickState==='ok'?'live':'warn'}>{tickState==='ok'?'HEALTHY':tickState==='stale'?'STALE':'STALLED'}</Pill>}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, height: '100%', justifyContent: 'center' }}>
            <div className="mono" style={{ fontSize: 24, fontWeight: 700, textAlign: 'center', color: 'var(--text)' }}>
              {monitorAge !== null ? `${monitorAge}s` : '—'}
            </div>
            <div style={{ fontSize: 10, color: 'var(--text-3)', textAlign: 'center', textTransform: 'uppercase', letterSpacing: 0.5 }}>
              {m.monitor.last_tick_iso ? new Date(m.monitor.last_tick_iso).toTimeString().slice(0,8) : 'waiting for tick'}
            </div>
            <div style={{ display: 'flex', justifyContent: 'center' }}>
              <Heartbeat history={m.monitor.history} />
            </div>
          </div>
        </Card>

        {/* Alerts */}
        <Card
          title="Alerts"
          icon="bell"
          subtitle={(() => {
            const unread = m.alerts.filter(a => {
              if (alertsAckedAt == null) return true;
              const t = a.time ? new Date(a.time).getTime() : 0;
              return t > alertsAckedAt;
            }).length;
            return unread > 0 ? `${unread} unread` : (m.alerts.length ? 'all read' : 'no alerts');
          })()}
          actions={
            <Btn
              variant="ghost"
              size="sm"
              disabled={!m.alerts.length}
              onClick={() => setAlertsAckedAt(Date.now())}
            >
              Mark all read
            </Btn>
          }
        >
          <div style={{ maxHeight: 150, overflowY: 'auto', margin: '-16px -20px' }}>
            {m.alerts.length === 0 && (
              <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>No active alerts</div>
            )}
            {m.alerts.map((a, i) => {
              const t = a.time ? new Date(a.time).getTime() : 0;
              const acked = alertsAckedAt != null && t <= alertsAckedAt;
              return (
                <div
                  key={i}
                  className={`alert ${a.level==='warn'?'warn':a.level==='crit'?'crit':'info'}`}
                  style={acked ? { opacity: 0.55 } : undefined}
                >
                  <div className="alert__icon"><Ico name={a.level==='crit'?'alert':'info'} size={12} /></div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="alert__title">{a.title}</div>
                    <div className="alert__msg">{a.msg}</div>
                  </div>
                  <div className="alert__time">{fmtTimeAgo(a.time)}</div>
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      {/* ── Row 3: Scanner Load Presets | Scanning Shows ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr', gap: 10, marginBottom: 10 }}>

        {/* Scanner – Load Presets */}
        <Card title="Scanner — Control" icon="radar" actions={
          <Btn variant="ghost" size="sm" icon="refresh" onClick={refreshPresets} />
        }>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, height: '100%', justifyContent: 'center' }}>
            <div className="field">
              <label>Select preset</label>
              <select className="sel" value={activePreset} onChange={e=>setActivePreset(e.target.value)}>
                <option value="">— choose preset —</option>
                {presetList.map(p=><option key={p.name} value={p.name}>{p.name}</option>)}
              </select>
            </div>
            <Btn variant={scannerRunning ? "danger" : "primary"}
                 icon={scannerRunning ? "stop" : "play"}
                 disabled={!activePreset}
                 onClick={toggleScanner}
                 style={{ width: '100%', justifyContent: 'center', height: 40, fontSize: 13, fontWeight: 600 }}>
              {scannerRunning ? 'Stop Scanner' : 'Start Scanner'}
            </Btn>
            {scannerMsg && <div style={{ fontSize: 11, color: 'var(--text-3)', textAlign: 'center' }}>{scannerMsg}</div>}
          </div>
        </Card>

        {/* Scanning Shows */}
        <Card title="Scanning Status" icon="target"
              subtitle={scannerRunning ? `active · ${activePreset}` : 'idle'}
              actions={
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <Btn variant="ghost" size="sm" icon="info" disabled={!activePreset} onClick={() => setShowPresetInfo(true)} />
                  <Pill kind={scannerRunning?'live':'off'}>{scannerRunning?'SCANNING':'IDLE'}</Pill>
                </div>
              }>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, fontSize: 12, height: '100%', placeContent: 'center' }}>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Active Preset</div>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{activePreset || '—'}</div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Scheduler</div>
              <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                {m.monitor.scheduler_jobs.length > 0 ?
                  m.monitor.scheduler_jobs.map(j=><Badge key={j} variant="info">{j}</Badge>) :
                  <span className="muted">none</span>
                }
              </div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Last Check</div>
              <div className="mono">{m.monitor.last_tick_iso ? fmtTimeAgo(m.monitor.last_tick_iso) : '—'}</div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Signals Today</div>
              <div className="mono" style={{ fontWeight: 700, color: 'var(--pos)', fontSize: 16 }}>
                {m.scanner.logs.filter(l=>l.signal).length}
              </div>
            </div>
          </div>
        </Card>
      </div>

      {/* ── Row 4: Signals Log | Positions ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>

        {/* Signals Log */}
        <Card title="Signals Log" icon="activity" flush
              actions={<Btn variant="ghost" size="sm" onClick={() => setLogClearedAt(Date.now())}>Clear</Btn>}>
          <div style={{ maxHeight: 220, overflowY: 'auto' }}>
            <div className="scan-row" style={{ background:'var(--bg-2)', fontWeight:600, fontSize:10, textTransform:'uppercase', letterSpacing:0.6, color:'var(--text-3)', position: 'sticky', top: 0, zIndex: 5 }}>
              <span>Time</span><span></span><span>Price</span><span>RSI</span><span>Message</span>
            </div>
            {(() => {
              const visibleLogs = logClearedAt
                ? m.scanner.logs.filter(l => {
                    const [h, mn, s] = (l.t || '00:00:00').split(':').map(Number);
                    const logMs = new Date().setHours(h, mn, s, 0);
                    return logMs > logClearedAt;
                  })
                : m.scanner.logs;
              if (visibleLogs.length === 0) {
                return <div style={{ padding:40, textAlign:'center', color:'var(--text-3)', fontSize:12 }}>
                  {scannerRunning ? 'Waiting for first scan…' : 'No scans yet — start a preset scan'}
                </div>;
              }
              return visibleLogs.map((l,i) => (
                <div key={i} className="scan-row">
                  <span className="t">{l.t}</span>
                  <span className={`dot ${l.signal?'hit':''}`} />
                  <span className="mono">${(l.price ?? 0).toFixed(2)}</span>
                  <span className="mono" style={{color:l.rsi_ok?'var(--pos)':'var(--text-3)'}}>{(l.rsi ?? 0).toFixed(1)}</span>
                  <span style={{color:l.signal?'var(--pos)':'var(--text-2)',fontWeight:l.signal?600:400}}>{l.msg}</span>
                </div>
              ));
            })()}
          </div>
        </Card>

        {/* Positions */}
        <Card title="Positions" icon="dashboard"
              subtitle={connected ? `${journalPositions.length} tracked` : 'not connected'}
              flush
              actions={connected && <Btn variant="ghost" size="sm" icon="refresh" onClick={async()=>{ const p=await safe(api.ibkrPositions); if(p?.positions) setIbkrPositions(p.positions); const j=await safe(api.openPositions); if(j?.positions) setJournalPositions(j.positions); }} />}>
          <div style={{ maxHeight: 220, overflowY: 'auto' }}>
            {!connected ? (
              <div style={{padding:60,textAlign:'center',color:'var(--text-3)',fontSize:12}}>
                Connect IBKR to see live positions
              </div>
            ) : (
              <table className="tbl">
                <thead style={{ position: 'sticky', top: 0, zIndex: 5 }}><tr>
                  <th>Symbol</th><th className="num">Qty</th>
                  <th className="num">Status</th><th className="num">Action</th>
                </tr></thead>
                <tbody>
                  {journalPositions.length === 0 && (
                    <tr><td colSpan="4" style={{textAlign:'center',padding:40,color:'var(--text-3)',fontSize:12}}>No open journal positions</td></tr>
                  )}
                  {journalPositions.map((p,i)=>(
                    <tr key={p.id}>
                      <td>
                        <div className="mono" style={{fontSize:11.5,fontWeight:600}}>{p.symbol}</div>
                        <div className="muted" style={{fontSize:10}}>{p.topology}</div>
                      </td>
                      <td className="num">{p.contracts}</td>
                      <td className="num">
                        <Badge variant={p.state==='open'?'pos':p.state==='closing'?'warn':'neutral'}>{p.state}</Badge>
                      </td>
                      <td className="num">
                        <Btn variant="danger" size="sm" icon="x" disabled={busy || p.state === 'closing'} onClick={() => exitPosition(p.id)}>
                          Close
                        </Btn>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Card>
      </div>

      {/* ── Row 5: Telegram bot control + status ── */}
      <Card
        title="Telegram Bot"
        icon="send"
        subtitle={
          tgStatus == null
            ? 'loading…'
            : tgStatus.configured
              ? `chat ${tgStatus.chat_id_masked} · polling every ${tgStatus.poll_interval_seconds}s`
              : 'set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars and restart'
        }
        actions={
          <Pill kind={tgStatus?.configured ? (tgStatus?.polling_active ? 'live' : 'warn') : 'off'}>
            {tgStatus?.configured
              ? (tgStatus?.polling_active ? 'ACTIVE' : 'IDLE')
              : 'NOT CONFIGURED'}
          </Pill>
        }
        style={{ marginBottom: 10 }}
      >
        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 14 }}>
          {/* Left: actions */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <input
                className="inp"
                placeholder={tgStatus?.configured ? 'Custom message to push to Telegram…' : 'Bot not configured'}
                value={tgCustom}
                onChange={e => setTgCustom(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey && tgCustom.trim() && tgStatus?.configured && !tgBusy) {
                    e.preventDefault();
                    sendTgCustom();
                  }
                }}
                disabled={!tgStatus?.configured || tgBusy}
                style={{ flex: 1 }}
              />
              <Btn
                variant="primary"
                icon="send"
                onClick={sendTgCustom}
                disabled={!tgStatus?.configured || tgBusy || !tgCustom.trim()}
              >
                Send
              </Btn>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Btn
                variant="ghost"
                size="sm"
                icon="check"
                onClick={sendTgTest}
                disabled={!tgStatus?.configured || tgBusy}
              >
                Send test message
              </Btn>
              {tgMsg && (
                <span style={{
                  fontSize: 11,
                  color: tgMsg.startsWith('✓') ? 'var(--pos)' : (tgMsg.startsWith('✗') ? 'var(--neg)' : 'var(--text-3)'),
                }}>
                  {tgMsg}
                </span>
              )}
            </div>
            {!tgStatus?.configured && (
              <div className="muted" style={{ fontSize: 11, lineHeight: 1.5 }}>
                <strong>Setup:</strong> talk to <span className="mono">@BotFather</span> on Telegram → <span className="mono">/newbot</span> → copy the token.
                Then DM your bot once and ask <span className="mono">@userinfobot</span> for your chat id.
                Add both to <span className="mono">config/.env</span> and restart the server.
              </div>
            )}
          </div>

          {/* Right: command reference */}
          <div>
            <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: 600, marginBottom: 6 }}>
              Available slash commands
            </div>
            {Array.isArray(tgStatus?.commands) && tgStatus.commands.length > 0 ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {tgStatus.commands.map(c => (
                  <span key={c} className="chip mono" style={{ fontSize: 10.5 }}>/{c}</span>
                ))}
              </div>
            ) : (
              <div className="muted" style={{ fontSize: 11 }}>
                Configure the bot to see registered commands.
              </div>
            )}
            <div className="muted" style={{ fontSize: 11, marginTop: 10, lineHeight: 1.5 }}>
              Notifications fire on: <strong>entry submitted/filled</strong>, <strong>risk rejection</strong>, <strong>exit fills</strong> (with realized P&L), <strong>FLATTEN ALL</strong>.
            </div>
          </div>
        </div>
      </Card>

      {/* ── Row 6: Order Ticket (full-width) ── */}
      <Card title="Order Ticket" icon="zap" subtitle={ticketMsg||'submit real combo order'}
            actions={<Badge variant={connected?'pos':'neutral'}>{connected?'READY':'CONNECT FIRST'}</Badge>}>
        <div className="ticket" style={{ padding: '4px 0' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px 120px', gap: 12 }}>
            <div className="field">
              <label>Strategy (from config)</label>
              <input className="inp" value={`${cfg.strategy_type} · ${cfg.topology}`} readOnly style={{ background: 'var(--bg-2)' }} />
            </div>
            <div className="field">
              <label>Target DTE</label>
              <input className="inp" type="number" value={targetDte} onChange={e=>setTargetDte(Number(e.target.value))} />
            </div>
            <div className="field">
              <label>Contracts (0=auto)</label>
              <input className="inp" type="number" value={contractsOverride} onChange={e=>setContractsOverride(Number(e.target.value))} />
            </div>
          </div>
          
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, margin: '14px 0', padding: '10px 14px', background: 'var(--bg-2)', borderRadius: 6 }}>
            <div className="muted" style={{fontSize:11, flex: 1}}>
              Spread cost target <strong style={{color:'var(--text)'}}>${cfg.spread_cost_target}</strong> ·
              Width ${cfg.strike_width} · Stop {cfg.stop_loss_pct}% / TP {cfg.take_profit_pct}% / TR {cfg.trailing_stop_pct}%
            </div>
            <div style={{display:'flex', gap:6}}>
              <Chip ok={m.risk.current_concurrent<m.risk.max_concurrent}>Cap {m.risk.current_concurrent}/{m.risk.max_concurrent}</Chip>
              <Chip ok={m.risk.daily_loss_used_pct<m.risk.daily_loss_limit_pct}>Daily loss</Chip>
              <Chip ok={m.risk.market_open}>Market</Chip>
              <Chip ok={!m.risk.event_blackout}>Event</Chip>
              <Chip ok={connected}>Connected</Chip>
            </div>
          </div>

          <div style={{display:'flex',gap:8}}>
            <Btn variant={chainPreview ? "primary" : "ghost"} size="lg" icon="radar" disabled={chainBusy} onClick={previewChain} style={{flex:1, justifyContent:'center', height: 44}}>
              {chainBusy ? 'Loading…' : chainPreview ? 'Refresh Preview' : 'Preview Option Chain'}
            </Btn>
            <Btn variant="primary" icon="send" disabled={busy||!connected} onClick={submitOrder} style={{flex:1.5, justifyContent:'center', height: 44, fontSize: 14, fontWeight: 700}}>
              {busy ? 'Submitting…' : 'Submit Combo LMT Order'}
            </Btn>
          </div>

          {chainPreview && (
            <div style={{marginTop: 12, padding:12, background:'var(--bg-0)', border: '1px solid var(--border)', borderRadius:8, color:'var(--text-2)'}}>
              {chainPreview.error ? (
                <div style={{ color: 'var(--neg)', fontSize: 12 }}>{chainPreview.error}</div>
              ) : (
                <>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, borderBottom: '1px solid var(--border-soft)', paddingBottom: 6 }}>
                    <span className="mono" style={{ fontWeight: 600, color: 'var(--text)' }}>${(chainPreview.price ?? 0).toFixed(2)}</span>
                    <span className="muted" style={{ fontSize: 11 }}>Expiration: <strong>{chainPreview.expiration}</strong></span>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
                    {chainPreview.legs?.length===0 ? <div className="muted" style={{ gridColumn: 'span 2' }}>No strikes near ATM</div> :
                      chainPreview.legs?.map((l,i)=>(
                        <div key={i} className="mono" style={{display:'flex',justifyContent:'space-between',fontSize:11, padding: '4px 8px', background: 'var(--bg-2)', borderRadius: 4}}>
                          <span style={{ color: 'var(--accent)', fontWeight: 600 }}>C {l.strike}</span>
                          <span>{(l.bid ?? 0).toFixed(2)} / {(l.ask ?? 0).toFixed(2)}</span>
                          <span className="muted" style={{fontSize: 10}}>{l.impliedVolatility != null ? `${(l.impliedVolatility * 100).toFixed(0)}% IV` : '— IV'}</span>
                        </div>
                      ))
                    }
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </Card>

      {/* Order confirmation modal */}
      {showTicket && (
        <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.6)',display:'grid',placeItems:'center',zIndex:200}} onClick={()=>setShowTicket(false)}>
          <div style={{background:'var(--bg-1)',border:'1px solid var(--border-strong)',borderRadius:10,padding:24,width:400,textAlign:'center'}} onClick={e=>e.stopPropagation()}>
            <Ico name="check" size={36} stroke={2.5} />
            <h3 style={{margin:'10px 0 4px'}}>Order submitted</h3>
            <p className="muted" style={{margin:0,fontSize:12}}>{ticketMsg}</p>
            <Btn variant="primary" onClick={()=>setShowTicket(false)} style={{marginTop:14}}>Dismiss</Btn>
          </div>
        </div>
      )}
      {/* Preset info modal */}
      {showPresetInfo && (
        <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.6)',display:'grid',placeItems:'center',zIndex:200}} onClick={()=>setShowPresetInfo(false)}>
          <div style={{background:'var(--bg-1)',border:'1px solid var(--border-strong)',borderRadius:10,padding:20,width:500,maxHeight:'80vh',overflowY:'auto'}} onClick={e=>e.stopPropagation()}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:14}}>
              <h3 style={{margin:0}}>Preset: {activePreset}</h3>
              <Btn variant="ghost" size="sm" icon="x" onClick={()=>setShowPresetInfo(false)} />
            </div>
            {(() => {
              const p = presetList.find(x => x.name === activePreset);
              if (!p) return <div className="muted">No data found</div>;
              return (
                <div style={{display:'flex',flexDirection:'column',gap:12,fontSize:12,textAlign:'left'}}>
                  <div>
                    <div className="muted" style={{fontSize:10,textTransform:'uppercase',fontWeight:700,marginBottom:4}}>Strategy & Logic</div>
                    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,background:'var(--bg-2)',padding:10,borderRadius:6}}>
                      <div>Ticker: <strong>{p.ticker}</strong></div>
                      <div>Engine: <strong>{p.strategy_name}</strong></div>
                      <div>Topology: <strong>{p.topology}</strong></div>
                      <div>Direction: <strong>{p.direction}</strong></div>
                      <div>Strike Width: <strong>${p.strike_width}</strong></div>
                      <div>Target DTE: <strong>{p.target_dte}</strong></div>
                    </div>
                  </div>

                  <div>
                    <div className="muted" style={{fontSize:10,textTransform:'uppercase',fontWeight:700,marginBottom:4}}>Parameters</div>
                    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,background:'var(--bg-2)',padding:10,borderRadius:6}}>
                      {Object.entries(p.strategy_params || {}).map(([k,v]) => (
                        <div key={k}>{k.replace(/_/g,' ')}: <strong>{v}</strong></div>
                      ))}
                    </div>
                  </div>

                  <div>
                    <div className="muted" style={{fontSize:10,textTransform:'uppercase',fontWeight:700,marginBottom:4}}>Filters</div>
                    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,background:'var(--bg-2)',padding:10,borderRadius:6}}>
                      {Object.entries(p.entry_filters || {}).map(([k,v]) => (
                        <div key={k}>{k.replace(/_/g,' ')}: <strong>{String(v)}</strong></div>
                      ))}
                    </div>
                  </div>

                  <div>
                    <div className="muted" style={{fontSize:10,textTransform:'uppercase',fontWeight:700,marginBottom:4}}>Risk & Execution</div>
                    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,background:'var(--bg-2)',padding:10,borderRadius:6}}>
                      <div>Cadence: <strong>{p.timing_mode} ({p.timing_value})</strong></div>
                      <div>Auto-execute: <strong>{String(p.auto_execute)}</strong></div>
                      <div>Stop Loss: <strong>{p.stop_loss_pct}%</strong></div>
                      <div>Take Profit: <strong>{p.take_profit_pct}%</strong></div>
                      <div>Trailing Stop: <strong>{p.trailing_stop_pct}%</strong></div>
                      <div>Mark-to-Market: <strong>{String(p.use_mark_to_market)}</strong></div>
                    </div>
                  </div>

                  <div>
                    <div className="muted" style={{fontSize:10,textTransform:'uppercase',fontWeight:700,marginBottom:4}}>Sizing</div>
                    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,background:'var(--bg-2)',padding:10,borderRadius:6}}>
                      <div>Method: <strong>{p.position_size_method}</strong></div>
                      {Object.entries(p.sizing_params || {}).map(([k,v]) => (
                        <div key={k}>{k.replace(/_/g,' ')}: <strong>{v}</strong></div>
                      ))}
                    </div>
                  </div>
                </div>
              );
            })()}
            <Btn variant="primary" onClick={()=>setShowPresetInfo(false)} style={{marginTop:20,width:'100%',justifyContent:'center'}}>Close</Btn>
          </div>
        </div>
      )}
    </div>
  );
}
