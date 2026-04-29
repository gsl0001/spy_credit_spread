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

const OCard = ({ title, icon, subtitle, children, flush }) => (
  <div style={{
    background: C.cardBg, border: `1px solid ${C.cardBorder}`,
    borderRadius: 10, overflow: 'hidden',
    marginBottom: flush ? 0 : undefined,
  }}>
    {title && (
      <div style={{ padding: '12px 16px', borderBottom: `1px solid ${C.cardBorder}`, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{title}</span>
        {subtitle && <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{subtitle}</span>}
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
  const [connected, setConnected] = useState(false);
  const [accId, setAccId] = useState('');
  const [connBusy, setConnBusy] = useState(false);
  const [connMsg, setConnMsg] = useState('');

  // Account
  const [account, setAccount] = useState(null);

  // Positions
  const [positions, setPositions] = useState([]);

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

  const persistConn = () => {
    localStorage.setItem('moomoo_host', host);
    localStorage.setItem('moomoo_port', port);
    localStorage.setItem('moomoo_pwd', tradePwd);
  };

  const doConnect = useCallback(async () => {
    if (connBusy) return;
    persistConn();
    setConnBusy(true);
    setConnMsg('Connecting…');
    try {
      const res = await api.moomoo.connect({ host, port: Number(port), trade_password: tradePwd });
      if (res?.connected) {
        setConnected(true);
        setAccId(res.acc_id ?? '');
        setAccount(res.account ?? null);
        setConnMsg(`✓ Connected (acc ${res.acc_id})`);
      } else {
        setConnMsg(`✗ ${res?.error || 'connect failed'}`);
      }
    } catch (e) {
      setConnMsg(`✗ ${e.message}`);
    } finally {
      setConnBusy(false);
    }
  }, [connBusy, host, port, tradePwd]);

  const doDisconnect = useCallback(async () => {
    try {
      await api.moomoo.disconnect();
    } catch (_) {}
    setConnected(false);
    setAccId('');
    setAccount(null);
    setConnMsg('Disconnected');
  }, []);

  const refreshAccount = useCallback(async () => {
    if (!connected) return;
    try {
      const res = await api.moomoo.account();
      if (!res?.error) setAccount(res);
    } catch (_) {}
  }, [connected]);

  const refreshPositions = useCallback(async () => {
    if (!connected) return;
    try {
      const res = await api.moomoo.positions();
      if (res?.positions) setPositions(res.positions);
    } catch (_) {}
  }, [connected]);

  useEffect(() => {
    if (!connected) return;
    refreshAccount();
    refreshPositions();
    const id = setInterval(() => { refreshAccount(); refreshPositions(); }, 15000);
    return () => clearInterval(id);
  }, [connected, refreshAccount, refreshPositions]);

  const doExecute = useCallback(async () => {
    if (execBusy || !connected) return;
    setExecBusy(true);
    setExecMsg('Placing order…');
    setExecResult(null);
    try {
      const res = await api.moomoo.execute({
        host, port: Number(port), trade_password: tradePwd,
        direction: ticketDir,
        contracts: Number(ticketQty),
        strike_width: Number(ticketWidth),
        target_dte: 0,
        spread_cost_target: Number(ticketDebit),
        otm_offset: Number(ticketOffset),
      });
      if (res?.success) {
        setExecMsg(`✓ Placed · K_long ${res.K_long} / K_short ${res.K_short} · ${res.contracts}c`);
        setExecResult(res);
        setOrderLog(prev => [{ time: new Date().toLocaleTimeString(), ...res, status: 'filled' }, ...prev.slice(0, 19)]);
        await refreshPositions();
      } else {
        setExecMsg(`✗ ${res?.reason || res?.error || 'order rejected'}`);
      }
    } catch (e) {
      setExecMsg(`✗ ${e.message}`);
    } finally {
      setExecBusy(false);
    }
  }, [execBusy, connected, host, port, tradePwd, ticketDir, ticketOffset, ticketWidth, ticketQty, ticketDebit, refreshPositions]);

  const doExit = useCallback(async (posId) => {
    try {
      const res = await api.moomoo.exit({ position_id: posId });
      await refreshPositions();
      return res;
    } catch (_) {}
  }, [refreshPositions]);

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
                <input {...inp(tradePwd, setTradePwd, 'password')} placeholder="PIN / trade password" />
              </div>
              <OBtn onClick={connected ? doDisconnect : doConnect} disabled={connBusy} danger={connected}>
                {connBusy ? '…' : connected ? 'Disconnect' : 'Connect'}
              </OBtn>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <StatusPill connected={connected} accId={accId} />
              {connMsg && (
                <span style={{ fontSize: 12, color: connMsg.startsWith('✓') ? C.pos : connMsg.startsWith('✗') ? C.neg : 'var(--text-3)' }}>
                  {connMsg}
                </span>
              )}
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

          {/* ── Positions table ── */}
          <OCard title="Positions" subtitle="moomoo account" flush>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Code</th><th>Side</th><th className="num">Qty</th>
                  <th className="num">Cost</th><th className="num">Mkt Val</th>
                  <th className="num">P&L</th><th></th>
                </tr>
              </thead>
              <tbody>
                {positions.length === 0 && (
                  <tr><td colSpan="7" style={{ textAlign: 'center', padding: 24, color: 'var(--text-3)' }}>No positions</td></tr>
                )}
                {positions.map((p, i) => {
                  const pnl = Number(p.pl_val ?? 0);
                  return (
                    <tr key={i}>
                      <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{p.code ?? '—'}</td>
                      <td><Badge variant={p.position_side === 'LONG' ? 'pos' : 'neg'} dot>{p.position_side ?? '—'}</Badge></td>
                      <td className="num">{Number(p.qty ?? 0)}</td>
                      <td className="num">{fmtUsd(p.cost_price ?? 0)}</td>
                      <td className="num">{fmtUsd(p.market_val ?? 0)}</td>
                      <td className="num" style={{ color: pnl >= 0 ? C.pos : C.neg }}>{fmtUsd(pnl, true)}</td>
                      <td>
                        <OBtn small danger onClick={() => doExit(p.position_id)}>Close</OBtn>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
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
