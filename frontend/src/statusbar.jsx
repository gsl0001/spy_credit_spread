import { useState, useEffect } from 'react';
import { Ico } from './icons.jsx';
import { api, safe } from './api.js';

function LiveClock() {
  const [time, setTime] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return <span className="mono">{time.toLocaleTimeString('en-US', { hour12: false })}</span>;
}

function TelegramPill() {
  const [tg, setTg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState('');

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      const r = await safe(api.telegramStatus, null);
      if (mounted) setTg(r);
    };
    load();
    const id = setInterval(load, 30000);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  if (!tg) return null;

  const sendTest = async (e) => {
    e.stopPropagation();
    if (!tg.configured || busy) return;
    setBusy(true); setFeedback('Sending…');
    try {
      const r = await api.telegramTest();
      setFeedback(r?.sent ? '✓ Sent' : `✗ ${r?.error || 'failed'}`);
    } catch (err) { setFeedback(`✗ ${err.message}`); }
    finally {
      setBusy(false);
      setTimeout(() => setFeedback(''), 2500);
    }
  };

  const dotClass = tg.configured ? (tg.polling_active ? 'live' : 'warn') : 'off';
  const label = tg.configured
    ? (tg.polling_active ? 'TG ON' : 'TG IDLE')
    : 'TG OFF';

  return (
    <div
      className="sb__group"
      onClick={sendTest}
      style={tg.configured ? { cursor: 'pointer' } : undefined}
      title={tg.configured
        ? `Telegram bot active (chat ${tg.chat_id_masked}). Click to send a test message.`
        : 'Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars and restart.'}
    >
      <span className={`sb__dot ${dotClass}`} />
      <span className="sb__label">{label}</span>
      {feedback && <span className="sb__muted">· {feedback}</span>}
    </div>
  );
}

export function Statusbar({ online, ibkr, moomoo, mkt, dailyPnl, openPositions }) {
  const ibkrLabel = ibkr === 'live' ? 'LIVE' : ibkr === 'warn' ? 'RECON' : 'OFF';
  const mooStatus = moomoo?.status || 'off';
  const mooLabel = moomoo?.reconnecting
    ? `RECON #${moomoo.attempt || 0}`
    : mooStatus === 'live' ? 'ON'
    : mooStatus === 'warn' ? 'STALE'
    : 'OFF';
  const pnlPos = dailyPnl >= 0;

  return (
    <footer className="statusbar">
      <div className="sb__group">
        <span className={`sb__dot ${online ? 'ok' : 'off'}`} />
        <span className="sb__label">API {online ? 'ON' : 'OFF'}</span>
      </div>

      <div className="sb__sep" />

      <div className="sb__group">
        <span className={`sb__dot ${ibkr}`} />
        <span className="sb__label">IBKR {ibkrLabel}</span>
      </div>

      <div className="sb__sep" />

      <div className="sb__group">
        <span className={`sb__dot ${mooStatus}`} />
        <span className="sb__label">MOOMOO {mooLabel}</span>
      </div>

      <div className="sb__sep" />

      <div className="sb__group">
        <span className={`sb__dot ${mkt.open ? 'ok' : 'off'}`} />
        <span className="sb__label">MKT {mkt.open ? 'OPEN' : 'CLOSED'}</span>
        {mkt.next && mkt.next !== '—' && (
          <span className="sb__muted">· {mkt.next}</span>
        )}
      </div>

      <div className="sb__sep" />

      <div className="sb__group">
        <Ico name="layers" size={11} />
        <span className="sb__label">{openPositions} position{openPositions !== 1 ? 's' : ''}</span>
      </div>

      <div className="sb__sep" />

      <div className="sb__group">
        <span className="sb__label">Day P&amp;L</span>
        <span className={`sb__val mono ${pnlPos ? 'pos' : 'neg'}`}>
          {pnlPos ? '+' : ''}{dailyPnl.toFixed(2)}
        </span>
      </div>

      <div className="sb__sep" />

      <TelegramPill />

      <div className="sb__spacer" />

      <div className="sb__group">
        <Ico name="clock" size={11} />
        <LiveClock />
      </div>
    </footer>
  );
}
