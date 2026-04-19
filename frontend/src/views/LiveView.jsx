import { useState, useEffect, useCallback } from 'react';
import { useData } from '../useBackendData.jsx';
import { Ico } from '../icons.jsx';
import { fmtUsd, fmtPct, fmtTimeAgo, Card, Kpi, Badge, Btn, Pill, Heartbeat, Chip } from '../primitives.jsx';
import { PriceChart } from '../chart.jsx';
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

  const flattenAll = useCallback(async () => {
    setBusy(true); setTicketMsg('Flattening all…');
    try {
      const res = await api.flatten();
      setTicketMsg(res?.error ? `Error: ${res.error}` : `Flatten submitted (${res?.closed || 0})`);
    } catch (e) { setTicketMsg(`Error: ${e.message}`); }
    finally { setBusy(false); }
  }, []);

  const accountDisplay = ibkrAccount || m.account;

  return (
    <div className="page">
      <Card title="IBKR connection" icon="radar" subtitle={connectMsg || (ibkrAccount ? 'connected' : 'not connected')} actions={
        <>
          <Pill kind={ibkrAccount ? 'live' : m.__ibkr === 'live' ? 'live' : 'off'}>
            {ibkrAccount ? 'CONNECTED' : (m.__ibkr || 'off').toUpperCase()}
          </Pill>
          <Btn size="sm" icon="activity" variant="primary" disabled={busy} onClick={connect}>Connect</Btn>
          <Btn size="sm" variant="danger" icon="zap" disabled={busy || !ibkrAccount} onClick={flattenAll}>Flatten all</Btn>
        </>
      }>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
          <div className="field"><label>Host</label><input className="inp" value={host} onChange={e => setHost(e.target.value)} /></div>
          <div className="field"><label>Port</label><input className="inp" type="number" value={port} onChange={e => setPort(Number(e.target.value))} /></div>
          <div className="field"><label>Client ID</label><input className="inp" type="number" value={clientId} onChange={e => setClientId(Number(e.target.value))} /></div>
        </div>
      </Card>

      <div className="grid g-4" style={{ marginBottom: 14, marginTop: 14 }}>
        <Card>
          <Kpi label="Equity" value={fmtUsd(accountDisplay.equity)}
            delta={{ text: fmtPct((accountDisplay.daily_pnl || 0) / (accountDisplay.equity || 1) * 100) + ' today',
              color: (accountDisplay.daily_pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)' }} big />
        </Card>
        <Card>
          <Kpi label="Day P&L" value={fmtUsd(accountDisplay.daily_pnl || 0, true)}
            color={(accountDisplay.daily_pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)'}
            delta={{ text: `${m.closed.filter(c => { const d = new Date() - c.closed; return d < 86400000; }).length} closed today` }} big />
        </Card>
        <Card>
          <Kpi label="Unrealized" value={fmtUsd(accountDisplay.unrealized_pnl || 0, true)}
            color={(accountDisplay.unrealized_pnl || 0) >= 0 ? 'var(--pos)' : 'var(--neg)'}
            delta={{ text: `${(ibkrPositions.length || m.positions.length)} open positions` }} big />
        </Card>
        <Card>
          <Kpi label="Buying Power" value={fmtUsd(accountDisplay.buying_power || 0)}
            delta={{ text: `${m.risk.buying_power_used_pct.toFixed(1)}% used · $${(accountDisplay.excess_liquidity || 0).toLocaleString()} excess` }} big />
        </Card>
      </div>

      <div className="grid g-23" style={{ marginBottom: 14 }}>
        <Card title="Monitor heartbeat" icon="activity" subtitle={m.monitor.tick_interval_sec ? `tick every ${m.monitor.tick_interval_sec}s` : 'monitor'} actions={
          <Pill kind={tickState === 'ok' ? 'live' : 'warn'}>
            <span className="dot" />{tickState === 'ok' ? 'HEALTHY' : tickState === 'stale' ? 'STALE' : 'STALLED'}
          </Pill>
        }>
          <div style={{ display: 'flex', alignItems: 'center', gap: 18, justifyContent: 'space-between', marginBottom: 12 }}>
            <div>
              <div style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Last tick</div>
              <div className="mono" style={{ fontSize: 18, fontWeight: 600 }}>{monitorAge !== null ? `${monitorAge}s ago` : '—'}</div>
              <div style={{ fontSize: 11, color: 'var(--text-4)' }}>{m.monitor.last_tick_iso ? new Date(m.monitor.last_tick_iso).toTimeString().slice(0, 8) : '—'}</div>
            </div>
            <div style={{ flex: 1 }}><Heartbeat history={m.monitor.history} /></div>
          </div>
          <hr className="sep" />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 16, fontSize: 12 }}>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 2 }}>Leader</div>
              <div style={{ fontWeight: 600 }}>{m.monitor.leader_info.host}</div>
              <div className="muted mono" style={{ fontSize: 11 }}>pid {m.monitor.leader_info.pid ?? '—'} · held {m.monitor.leader_info.acquired_at ? fmtTimeAgo(m.monitor.leader_info.acquired_at) : '—'}</div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 2 }}>Scheduler jobs</div>
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {m.monitor.scheduler_jobs.map(j => <Badge key={j} variant="info">{j}</Badge>)}
              </div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 2 }}>Fill watcher</div>
              <div style={{ fontWeight: 600 }}>{m.orders.filter(o => o.status === 'pending').length} pending · {m.orders.filter(o => o.status === 'cancelling').length} cancelling</div>
              <div className="muted" style={{ fontSize: 11 }}>{m.monitor.monitor_registered ? 'registered' : 'not registered'}</div>
            </div>
          </div>
        </Card>

        <Card title="Alerts" icon="bell" actions={<Btn variant="ghost" size="sm">Mark all read</Btn>}>
          <div className="alerts" style={{ margin: '-16px -20px' }}>
            {m.alerts.length === 0 && <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>No active alerts</div>}
            {m.alerts.map((a, i) => (
              <div key={i} className={`alert ${a.level === 'warn' ? 'warn' : a.level === 'crit' ? 'crit' : 'info'}`}>
                <div className="alert__icon">
                  <Ico name={a.level === 'warn' ? 'alert' : a.level === 'crit' ? 'alert' : 'info'} size={12} />
                </div>
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

      <div className="grid g-32" style={{ marginBottom: 14 }}>
        <Card title="SPY · intraday" icon="activity" flush>
          <PriceChart series={m.spy.series} height={300} />
        </Card>

        <Card title="Order ticket" icon="zap" subtitle={ticketMsg || 'submit real combo order'} actions={
          <Badge variant={ibkrAccount ? 'pos' : 'neutral'}>{ibkrAccount ? 'READY' : 'CONNECT FIRST'}</Badge>
        }>
          <div className="ticket">
            <div style={{ display: 'flex', gap: 8 }}>
              <div className="field" style={{ flex: 1 }}>
                <label>Strategy (from config)</label>
                <input className="inp" value={`${loadConfig().strategy_type} · ${loadConfig().topology}`} readOnly />
              </div>
              <div className="field" style={{ width: 90 }}>
                <label>Target DTE</label>
                <input className="inp" type="number" value={targetDte} onChange={e => setTargetDte(Number(e.target.value))} />
              </div>
              <div className="field" style={{ width: 110 }}>
                <label>Contracts (0=auto)</label>
                <input className="inp" type="number" value={contractsOverride} onChange={e => setContractsOverride(Number(e.target.value))} />
              </div>
            </div>
            <hr className="sep" style={{ margin: 0 }} />
            <div className="muted" style={{ fontSize: 11 }}>
              Spread cost target <strong style={{ color: 'var(--text)' }}>${loadConfig().spread_cost_target}</strong> ·
              Width ${loadConfig().strike_width} · Stop {loadConfig().stop_loss_pct}% / TP {loadConfig().take_profit_pct}% / TR {loadConfig().trailing_stop_pct}%
            </div>
            <hr className="sep" style={{ margin: '4px 0' }} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 11.5 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Chip ok={m.risk.current_concurrent < m.risk.max_concurrent}>Concurrent cap ({m.risk.current_concurrent}/{m.risk.max_concurrent})</Chip>
                <Chip ok={m.risk.daily_loss_used_pct < m.risk.daily_loss_limit_pct}>Daily loss</Chip>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Chip ok={m.risk.market_open}>Market hours</Chip>
                <Chip ok={!m.risk.event_blackout}>Event window</Chip>
                <Chip ok={!!ibkrAccount}>IBKR connected</Chip>
              </div>
            </div>
            <Btn variant="primary" icon="send" disabled={busy || !ibkrAccount} onClick={submitOrder} style={{ justifyContent: 'center', padding: '10px' }}>
              {busy ? 'Submitting…' : 'Submit combo LMT'}
            </Btn>
            {ticketMsg && (
              <div style={{ fontSize: 11, padding: 8, background: 'var(--bg-2)', borderRadius: 6, color: 'var(--text-2)' }}>{ticketMsg}</div>
            )}
          </div>
        </Card>
      </div>

      <Card title="Open positions" icon="dashboard" subtitle={`${ibkrPositions.length || m.positions.length} live`} flush actions={
        <Btn variant="ghost" size="sm" icon="refresh" onClick={async () => { const p = await safe(api.ibkrPositions); if (p?.positions) setIbkrPositions(p.positions); }} />
      }>
        <table className="tbl">
          <thead><tr>
            <th>Symbol</th><th>Position</th><th className="num">Qty</th>
            <th className="num">Avg cost</th><th className="num">Mkt value</th>
            <th className="num">Unrealized</th>
          </tr></thead>
          <tbody>
            {ibkrPositions.length === 0 && m.positions.length === 0 && (
              <tr><td colSpan="6" style={{ textAlign: 'center', padding: 32, color: 'var(--text-3)', fontSize: 12 }}>No open positions</td></tr>
            )}
            {ibkrPositions.map((p, i) => (
              <tr key={i}>
                <td><div className="mono" style={{ fontSize: 11.5, fontWeight: 600 }}>{p.symbol}</div><div className="muted" style={{ fontSize: 10.5 }}>{p.sec_type || p.secType}</div></td>
                <td><span className="mono">{p.local_symbol || p.localSymbol || '—'}</span></td>
                <td className="num">{p.position}</td>
                <td className="num">${(p.avg_cost ?? 0).toFixed(2)}</td>
                <td className="num">${(p.market_value ?? 0).toFixed(2)}</td>
                <td className="num" style={{ color: (p.unrealized_pnl ?? 0) >= 0 ? 'var(--pos)' : 'var(--neg)', fontWeight: 600 }}>
                  {fmtUsd(p.unrealized_pnl ?? 0, true)}
                </td>
              </tr>
            ))}
            {ibkrPositions.length === 0 && m.positions.map(p => (
              <tr key={p.id}>
                <td><div className="mono" style={{ fontSize: 11.5, fontWeight: 600 }}>{p.symbol}</div><div className="muted" style={{ fontSize: 10.5 }}>{p.topology}</div></td>
                <td><span className="mono">{p.legs}</span></td>
                <td className="num">{p.contracts}</td>
                <td className="num">${p.entry_cost.toFixed(2)}</td>
                <td className="num">${p.mtm.toFixed(2)}</td>
                <td className="num" style={{ color: p.pnl >= 0 ? 'var(--pos)' : 'var(--neg)', fontWeight: 600 }}>{fmtUsd(p.pnl, true)} <span style={{ opacity: 0.7, fontSize: 11 }}>({fmtPct(p.pnl_pct)})</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {showTicket && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'grid', placeItems: 'center', zIndex: 200 }} onClick={() => setShowTicket(false)}>
          <div style={{ background: 'var(--bg-1)', border: '1px solid var(--border-strong)', borderRadius: 10, padding: 24, width: 400, textAlign: 'center' }} onClick={e => e.stopPropagation()}>
            <Ico name="check" size={36} stroke={2.5} />
            <h3 style={{ margin: '10px 0 4px' }}>Order submitted</h3>
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>{ticketMsg}</p>
            <Btn variant="primary" onClick={() => setShowTicket(false)} style={{ marginTop: 14 }}>Dismiss</Btn>
          </div>
        </div>
      )}
    </div>
  );
}
