import { useState, useCallback } from 'react';
import { useData } from '../useBackendData.jsx';
import { fmtTimeAgo, Card, Badge, Btn, Chip, RiskBar } from '../primitives.jsx';

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

export function RiskView() {
  const m = useData(), r = m.risk;
  // User requested to pull data at all hours; indicator reflects the bypass.
  const ignoreRTH = true;

  const [digestBusy, setDigestBusy] = useState(false);
  const [digestMsg, setDigestMsg] = useState('');

  const sendTestDigest = useCallback(async () => {
    if (digestBusy) return;
    setDigestBusy(true);
    setDigestMsg('Sending…');
    try {
      const res = await fetch(`${API}/api/notify/digest`, { method: 'POST' });
      const data = await res.json();
      setDigestMsg(data?.sent ? '✓ Digest sent' : '✗ Webhook URL not configured');
    } catch (e) {
      setDigestMsg(`✗ ${e.message}`);
    } finally {
      setDigestBusy(false);
      setTimeout(() => setDigestMsg(''), 4000);
    }
  }, [digestBusy]);
  return (
    <div className="page">
      <div className="grid g-3" style={{ marginBottom: 14 }}>
        <Card title="Pre-trade gate" icon="shield" actions={<Badge variant="pos" dot>ACTIVE</Badge>}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Market hours</span><Chip ok={r.market_open || ignoreRTH}>{ignoreRTH ? '24/7 (Ignore RTH)' : r.market_open ? `Open · closes ${r.next_close || '—'}` : 'Closed'}</Chip></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Event blackout</span><Chip ok={!r.event_blackout}>{r.event_blackout ? 'BLOCKED' : 'Clear'}</Chip></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Leader lock</span><Chip ok={m.monitor.is_leader}>{m.monitor.is_leader ? 'Held' : 'Not held'}</Chip></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Buying power</span><Chip ok={(m.account.buying_power ?? 0) > 0}>${(m.account.buying_power ?? 0).toLocaleString()}</Chip></div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>IBKR socket</span><Chip ok={m.__ibkr === 'live'}>{m.__ibkr === 'live' ? 'Connected' : m.__ibkr === 'warn' ? 'Reconnecting' : 'Disconnected'}</Chip></div>
          </div>
        </Card>

        <Card title="Capacity" icon="target">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <RiskBar label="Concurrent positions" used={r.current_concurrent ?? 0} limit={r.max_concurrent ?? 1} unit="" />
            <RiskBar
              label="Daily loss (of cap)"
              used={r.daily_loss_limit_pct ? +((r.daily_loss_used_pct ?? 0) / r.daily_loss_limit_pct * 100).toFixed(1) : 0}
              limit={100}
              unit="%"
            />
            <RiskBar label="Buying power used" used={+((r.buying_power_used_pct ?? 0).toFixed(1))} limit={100} unit="%" />
          </div>
        </Card>

        <Card title="Sizing" icon="sliders">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 12 }}>
            <div className="ticket__row"><span className="k">Mode</span><span className="v">{r.sizing_mode}</span></div>
            <div className="ticket__row"><span className="k">Target % of equity</span><span className="v">{(r.target_pct ?? 0).toFixed(2)}%</span></div>
            <div className="ticket__row"><span className="k">Max trade cap</span><span className="v">${r.max_trade_cap ?? 0}</span></div>
            <div className="ticket__row"><span className="k">Bid-ask haircut</span><span className="v">{r.haircut_bps ?? 0} bps</span></div>
            <hr className="sep" style={{ margin: '4px 0' }} />
            <div className="muted" style={{ fontSize: 11 }}>Sizing clamped by excess_liquidity ${(m.account.excess_liquidity ?? 0).toLocaleString()}</div>
          </div>
        </Card>
      </div>

      <div className="grid g-12" style={{ marginBottom: 14 }}>
        <Card title="Next event window" icon="calendar">
          <div style={{ textAlign: 'center', padding: '12px 0' }}>
            <div style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: 0.6 }}>{r.next_event.name} · {r.next_event.date}</div>
            <div style={{ fontSize: 40, fontWeight: 700, fontFamily: 'var(--font-mono)', letterSpacing: -1 }}>
              {r.next_event.days_until}<span style={{ fontSize: 16, color: 'var(--text-3)', marginLeft: 4 }}>days</span>
            </div>
            <div className="muted" style={{ fontSize: 11 }}>blackout window {r.next_event.window}</div>
          </div>
        </Card>

        <Card title="Event calendar" icon="calendar" flush>
          <table className="tbl">
            <thead><tr><th>Date</th><th>Event</th><th>Status</th><th>Blocks trades</th><th></th></tr></thead>
            <tbody>
              {(!m.events || m.events.length === 0) && (
                <tr><td colSpan="5" style={{ textAlign: 'center', padding: 24, color: 'var(--text-3)', fontSize: 12 }}>No events configured</td></tr>
              )}
              {(m.events || []).map((ev, i) => (
                <tr key={i}>
                  <td className="mono">{ev.date}</td>
                  <td><Badge variant={ev.type === 'FOMC' ? 'warn' : ev.type === 'CPI' ? 'info' : 'neutral'}>{ev.type}</Badge></td>
                  <td><Badge variant={ev.status === 'upcoming' ? 'warn' : 'neutral'} dot>{ev.status}</Badge></td>
                  <td className="muted" style={{ fontSize: 11 }}>{ev.blocks || '—'}</td>
                  <td style={{ textAlign: 'right' }}><Btn variant="ghost" size="sm">Details</Btn></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <Card
        title="Daily digest webhook"
        icon="send"
        actions={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {digestMsg && (
              <span style={{
                fontSize: 11,
                color: digestMsg.startsWith('✓') ? 'var(--pos)' : digestMsg.startsWith('✗') ? 'var(--neg)' : 'var(--text-3)',
              }}>
                {digestMsg}
              </span>
            )}
            <Btn size="sm" icon="send" disabled={digestBusy} onClick={sendTestDigest}>
              {digestBusy ? 'Sending…' : 'Send test'}
            </Btn>
          </div>
        }
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, fontSize: 12 }}>
          <div><div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Channel</div><div style={{ fontWeight: 600 }}>{m.digest.channel || '—'}</div></div>
          <div><div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Schedule</div><div className="mono">{m.digest.cron || '—'}</div></div>
          <div><div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Last sent</div><div>{fmtTimeAgo(m.digest.last_sent)}{m.digest.last_status ? ` · HTTP ${m.digest.last_status}` : ''}</div></div>
          <div><div className="muted" style={{ fontSize: 10, textTransform: 'uppercase' }}>Status</div><Badge variant={m.digest.url_set ? 'pos' : 'neutral'} dot>{m.digest.url_set ? 'Configured' : 'Not configured'}</Badge></div>
        </div>
      </Card>
    </div>
  );
}
