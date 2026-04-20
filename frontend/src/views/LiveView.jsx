import { useState, useEffect, useCallback, useMemo } from 'react';
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
        const posRes = await safe(api.ibkrPositions);
        if (posRes?.positions) setIbkrPositions(posRes.positions);
      } else {
        setConnectMsg(res?.error || 'Connect failed');
      }
    } catch (e) { setConnectMsg(`Error: ${e.message}`); }
    finally { setBusy(false); }
  }, [host, port, clientId]);

  useEffect(() => {
    if (!ibkrAccount) return;
    const poll = async () => {
      const [a, p] = await Promise.all([safe(api.ibkrConnect), safe(api.ibkrPositions)]);
      if (a?.connected) setIbkrAccount(a.summary);
      if (p?.positions) setIbkrPositions(p.positions);
    };
    const id = setInterval(poll, 30000);
    return () => clearInterval(id);
  }, [ibkrAccount]);

  useEffect(() => {
    // API returns { presets: [...] } — unwrap the wrapper
    safe(api.presetsList, {}).then(r => setPresetList(Array.isArray(r?.presets) ? r.presets : []));
  }, []);

  const submitOrder = useCallback(async () => {
    setBusy(true); setTicketMsg('Submitting order…');
    try {
      const cfg = loadConfig();
      const payload = {
        symbol: cfg.ticker,
        topology: cfg.topology,
        direction: cfg.strategy_type === 'bear_put' ? 'bear_put' : 'bull_call',
        contracts: Number(contractsOverride) || 0,
        strike_width: cfg.strike_width,
        target_dte: Number(targetDte) || cfg.target_dte,
        spread_cost_target: cfg.spread_cost_target,
        stop_loss_pct: cfg.stop_loss_pct,
        take_profit_pct: cfg.take_profit_pct,
        trailing_stop_pct: cfg.trailing_stop_pct,
      };
      const res = await api.ibkrExecute(payload);
      if (res?.error) setTicketMsg(`Error: ${res.error}`);
      else { setTicketMsg(`Submitted ${res.contracts || ''} contracts · order ${res.order_id || ''}`); setShowTicket(true); }
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

  // Only show financial figures that came from IBKR — show '—' from MOCK zeros
  const connected = !!ibkrAccount;
  const acct = ibkrAccount || {};
  const cfg = useMemo(() => loadConfig(), []);   // cache once per render cycle

  return (
    <div className="page">

      {/* ── Row 1: Account Info | IBKR Connection ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10, marginBottom: 10 }}>

        {/* Account Info */}
        <Card title="Account Info" icon="dashboard"
              subtitle={connected ? `Account ${acct.account_id || ''}` : 'connect IBKR to load'}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10 }}>
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
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
              <div className="field"><label>Host</label><input className="inp" value={host} onChange={e=>setHost(e.target.value)} /></div>
              <div className="field"><label>Port</label><input className="inp" type="number" value={port} onChange={e=>setPort(Number(e.target.value))} /></div>
              <div className="field"><label>Client ID</label><input className="inp" type="number" value={clientId} onChange={e=>setClientId(Number(e.target.value))} /></div>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <Btn variant="primary" icon="activity" disabled={busy} onClick={connect} style={{flex:1}}>Connect</Btn>
              <Btn variant="danger" icon="zap" disabled={busy||!connected} onClick={flattenAll} style={{flex:1}}>Flatten all</Btn>
            </div>
          </div>
        </Card>
      </div>

      {/* ── Row 2: Monitor Heartbeat | Kill Switch | Alerts ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'auto auto 1fr', gap: 10, marginBottom: 10 }}>

        {/* Monitor Heartbeat */}
        <Card title="Monitor Heartbeat" icon="activity" style={{ minWidth: 180 }}
              actions={<Pill kind={tickState==='ok'?'live':'warn'}>{tickState==='ok'?'HEALTHY':tickState==='stale'?'STALE':'STALLED'}</Pill>}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div className="mono" style={{ fontSize: 22, fontWeight: 700, textAlign: 'center' }}>
              {monitorAge !== null ? `${monitorAge}s` : '—'}
            </div>
            <div style={{ fontSize: 10, color: 'var(--text-3)', textAlign: 'center' }}>
              {m.monitor.last_tick_iso ? new Date(m.monitor.last_tick_iso).toTimeString().slice(0,8) : 'no tick'}
            </div>
            <Heartbeat history={m.monitor.history} />
          </div>
        </Card>

        {/* Kill Switch */}
        <Card title="Kill Switch" icon="zap" style={{ minWidth: 120 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center', padding: '4px 0' }}>
            <Btn variant="danger" icon="zap" disabled={busy||!connected} onClick={flattenAll}
                 style={{ width: '100%', justifyContent: 'center', padding: '12px 0', fontWeight: 700, fontSize: 13 }}>
              FLATTEN ALL
            </Btn>
            <div style={{ fontSize: 10, color: 'var(--text-3)', textAlign: 'center' }}>
              {connected ? 'Ready — closes all open positions' : 'Connect IBKR first'}
            </div>
          </div>
        </Card>

        {/* Alerts */}
        <Card title="Alerts" icon="bell" actions={<Btn variant="ghost" size="sm">Mark all read</Btn>}>
          <div style={{ maxHeight: 140, overflowY: 'auto', margin: '-16px -20px' }}>
            {m.alerts.length === 0 && (
              <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>No active alerts</div>
            )}
            {m.alerts.map((a, i) => (
              <div key={i} className={`alert ${a.level==='warn'?'warn':a.level==='crit'?'crit':'info'}`}>
                <div className="alert__icon"><Ico name={a.level==='crit'?'alert':'info'} size={12} /></div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="alert__title">{a.title}</div>
                  <div className="alert__msg">{a.msg}</div>
                </div>
                <div className="alert__time">{fmtTimeAgo(a.time)}</div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* ── Row 3: Scanner Load Presets | Scanning Shows ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>

        {/* Scanner – Load Presets */}
        <Card title="Scanner — Load Presets" icon="radar">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="field">
              <label>Select preset</label>
              <select className="sel" value={activePreset} onChange={e=>setActivePreset(e.target.value)}>
                <option value="">— choose preset —</option>
                {presetList.map(p=><option key={p.name} value={p.name}>{p.name}</option>)}
              </select>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <Btn variant="primary" icon="play" disabled={!activePreset||scannerRunning} onClick={startPreset} style={{flex:1}}>
                Start
              </Btn>
              <Btn variant="danger" icon="pause" disabled={!scannerRunning} onClick={stopPreset} style={{flex:1}}>
                Stop
              </Btn>
            </div>
            {scannerMsg && <div style={{ fontSize: 11, color: 'var(--text-3)' }}>{scannerMsg}</div>}
          </div>
        </Card>

        {/* Scanning Shows */}
        <Card title="Scanning Shows" icon="target"
              subtitle={scannerRunning ? `active · ${activePreset}` : 'idle'}
              actions={<Pill kind={scannerRunning?'live':'off'}>{scannerRunning?'SCANNING':'IDLE'}</Pill>}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, fontSize: 12 }}>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Preset</div>
              <div style={{ fontWeight: 600 }}>{activePreset || '—'}</div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Scanner jobs</div>
              <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                {m.monitor.scheduler_jobs.map(j=><Badge key={j} variant="info">{j}</Badge>)}
              </div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Last tick</div>
              <div className="mono">{m.monitor.last_tick_iso ? fmtTimeAgo(m.monitor.last_tick_iso) : '—'}</div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Signals today</div>
              <div className="mono" style={{ fontWeight: 600, color: 'var(--pos)' }}>
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
            <div className="scan-row" style={{ background:'var(--bg-2)', fontWeight:600, fontSize:10, textTransform:'uppercase', letterSpacing:0.6, color:'var(--text-3)' }}>
              <span>Time</span><span></span><span>Price</span><span>RSI</span><span>Message</span>
            </div>
            {(() => {
              // Filter out logs that arrived before user hit Clear
              const visibleLogs = logClearedAt
                ? m.scanner.logs.filter(l => {
                    // l.t is HH:MM:SS — compare against today's clearedAt timestamp
                    const [h, mn, s] = (l.t || '00:00:00').split(':').map(Number);
                    const logMs = new Date().setHours(h, mn, s, 0);
                    return logMs > logClearedAt;
                  })
                : m.scanner.logs;
              if (visibleLogs.length === 0) {
                return <div style={{ padding:20, textAlign:'center', color:'var(--text-3)', fontSize:12 }}>
                  {scannerRunning ? 'Waiting for first scan…' : 'No scans yet — start a preset scan'}
                </div>;
              }
              return visibleLogs.map((l,i) => (
                <div key={i} className="scan-row">
                  <span className="t">{l.t}</span>
                  <span className={`dot ${l.signal?'hit':''}`} />
                  <span className="mono">${l.price.toFixed(2)}</span>
                  <span className="mono" style={{color:l.rsi_ok?'var(--pos)':'var(--text-3)'}}>{l.rsi.toFixed(1)}</span>
                  <span style={{color:l.signal?'var(--pos)':'var(--text-2)',fontWeight:l.signal?600:400}}>{l.msg}</span>
                </div>
              ));
            })()}
          </div>
        </Card>

        {/* Positions — only show IBKR live positions when connected */}
        <Card title="Positions" icon="dashboard"
              subtitle={connected ? `${ibkrPositions.length} live` : 'not connected'}
              flush
              actions={connected && <Btn variant="ghost" size="sm" icon="refresh" onClick={async()=>{ const p=await safe(api.ibkrPositions); if(p?.positions) setIbkrPositions(p.positions); }} />}>
          <div style={{ maxHeight: 220, overflowY: 'auto' }}>
            {!connected ? (
              <div style={{padding:32,textAlign:'center',color:'var(--text-3)',fontSize:12}}>
                Connect IBKR to see live positions
              </div>
            ) : (
              <table className="tbl">
                <thead><tr>
                  <th>Symbol</th><th className="num">Qty</th>
                  <th className="num">Avg cost</th><th className="num">Unrealized</th>
                </tr></thead>
                <tbody>
                  {ibkrPositions.length === 0 && (
                    <tr><td colSpan="4" style={{textAlign:'center',padding:24,color:'var(--text-3)',fontSize:12}}>No open positions</td></tr>
                  )}
                  {ibkrPositions.map((p,i)=>(
                    <tr key={i}>
                      <td><div className="mono" style={{fontSize:11.5,fontWeight:600}}>{p.symbol}</div><div className="muted" style={{fontSize:10}}>{p.sec_type||p.secType}</div></td>
                      <td className="num">{p.position}</td>
                      <td className="num">${(p.avg_cost??0).toFixed(2)}</td>
                      <td className="num" style={{color:(p.unrealized_pnl??0)>=0?'var(--pos)':'var(--neg)',fontWeight:600}}>{fmtUsd(p.unrealized_pnl??0,true)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Card>
      </div>

      {/* ── Row 5: Order Ticket (full-width) ── */}
      <Card title="Order Ticket" icon="zap" subtitle={ticketMsg||'submit real combo order'}
            actions={<Badge variant={connected?'pos':'neutral'}>{connected?'READY':'CONNECT FIRST'}</Badge>}>
        <div className="ticket">
          <div style={{ display: 'flex', gap: 8 }}>
            <div className="field" style={{flex:1}}>
              <label>Strategy (from config)</label>
              <input className="inp" value={`${cfg.strategy_type} · ${cfg.topology}`} readOnly />
            </div>
            <div className="field" style={{width:90}}>
              <label>Target DTE</label>
              <input className="inp" type="number" value={targetDte} onChange={e=>setTargetDte(Number(e.target.value))} />
            </div>
            <div className="field" style={{width:110}}>
              <label>Contracts (0=auto)</label>
              <input className="inp" type="number" value={contractsOverride} onChange={e=>setContractsOverride(Number(e.target.value))} />
            </div>
          </div>
          <hr className="sep" style={{margin:0}} />
          <div className="muted" style={{fontSize:11}}>
            Spread cost target <strong style={{color:'var(--text)'}}>${cfg.spread_cost_target}</strong> ·
            Width ${cfg.strike_width} · Stop {cfg.stop_loss_pct}% / TP {cfg.take_profit_pct}% / TR {cfg.trailing_stop_pct}%
          </div>
          <hr className="sep" style={{margin:'4px 0'}} />
          <div style={{display:'flex',flexDirection:'column',gap:6,fontSize:11.5}}>
            <div style={{display:'flex',alignItems:'center',gap:8}}>
              <Chip ok={m.risk.current_concurrent<m.risk.max_concurrent}>Concurrent cap ({m.risk.current_concurrent}/{m.risk.max_concurrent})</Chip>
              <Chip ok={m.risk.daily_loss_used_pct<m.risk.daily_loss_limit_pct}>Daily loss</Chip>
              <Chip ok={m.risk.market_open}>Market hours</Chip>
              <Chip ok={!m.risk.event_blackout}>Event window</Chip>
              <Chip ok={connected}>IBKR connected</Chip>
            </div>
          </div>
          <div style={{display:'flex',gap:6}}>
            <Btn variant="ghost" size="sm" icon="radar" disabled={chainBusy} onClick={previewChain} style={{flex:1,justifyContent:'center'}}>
              {chainBusy?'Loading…':'Preview chain'}
            </Btn>
            <Btn variant="primary" icon="send" disabled={busy||!connected} onClick={submitOrder} style={{flex:2,justifyContent:'center',padding:'10px'}}>
              {busy?'Submitting…':'Submit combo LMT'}
            </Btn>
          </div>
          {chainPreview && (
            <div style={{fontSize:11,padding:8,background:'var(--bg-2)',borderRadius:6,color:'var(--text-2)'}}>
              {chainPreview.error ? `Chain error: ${chainPreview.error}` : (
                <>
                  <div className="mono" style={{marginBottom:4}}>${chainPreview.price?.toFixed(2)} · exp {chainPreview.expiration}</div>
                  {chainPreview.legs?.length===0 && <div className="muted">No strikes near ATM</div>}
                  {chainPreview.legs?.map((l,i)=>(
                    <div key={i} className="mono" style={{display:'flex',justifyContent:'space-between',fontSize:10.5}}>
                      <span>C {l.strike}</span>
                      <span>bid {l.bid?.toFixed(2)} / ask {l.ask?.toFixed(2)}</span>
                      <span className="muted">IV {(l.impliedVolatility*100)?.toFixed(0)}%</span>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
          {ticketMsg && <div style={{fontSize:11,padding:8,background:'var(--bg-2)',borderRadius:6,color:'var(--text-2)'}}>{ticketMsg}</div>}
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
    </div>
  );
}
