import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { api, safe, IBKR_CREDS } from './api.js';
import { MOCK } from './data.js';

const DataContext = createContext(MOCK);

export const useData = () => useContext(DataContext);

const POLL_MS = {
  heartbeat: 5000,
  scanner:   3000,
  positions: 10000,
  pnl:       30000,
  events:    15000,
  orders:    10000,
  spy:       15000,
  chain:     60000,
};

function mergeSpy(src, spyRes) {
  if (!spyRes || spyRes.error) return src;
  const series = (spyRes.data || []).map(d => d.close);
  return {
    current: spyRes.current ?? 0,
    change: spyRes.change ?? 0,
    change_pct: spyRes.change_pct ?? 0,
    series,
    source: spyRes.source || 'yfinance',
  };
}

function mergeMonitor(src, hb) {
  if (!hb) return src;
  return {
    ...src,
    is_leader: hb.is_leader ?? false,
    leader_info: hb.leader_info || src.leader_info,
    last_tick_iso: hb.monitor_last_tick_iso ?? null,
    seconds_since_tick: hb.monitor_seconds_since_tick ?? null,
    monitor_registered: hb.monitor_registered ?? false,
    scheduler_jobs: hb.scheduler_jobs ?? [],
    history: src.history.length
      ? [...src.history.slice(1), { t: Date.now(), state: hb.monitor_stalled ? 'stale' : hb.alive ? 'ok' : 'fail' }]
      : Array.from({ length: 40 }, (_, i) => ({ t: i, state: hb.alive ? 'ok' : 'fail' })),
  };
}

function mergeAccount(src, hb) {
  if (!hb) return src;
  return {
    ...src,
    daily_pnl: hb.today_pnl ?? 0,
    open_positions: hb.open_positions ?? 0,
  };
}

function mergeRisk(src, hb) {
  if (!hb) return src;
  return {
    ...src,
    daily_loss_limit_pct: hb.daily_loss_limit_pct ?? src.daily_loss_limit_pct,
    daily_loss_used_pct: hb.daily_loss_pct_used ?? 0,
    current_concurrent: hb.open_positions ?? 0,
  };
}

function mergeAlerts(hb) {
  if (!hb?.alerts?.length) return [];
  return hb.alerts.map((a, i) => ({
    level: a.level === 'warning' ? 'warn' : a.level || 'info',
    code: a.code || `alert_${i}`,
    title: a.code ? a.code.replace(/_/g, ' ') : 'Alert',
    msg: a.message || '',
    time: new Date(),
  }));
}

function mergePositions(src, res) {
  if (!res?.positions) return src;
  return res.positions.map(p => ({
    id: p.id,
    symbol: p.symbol,
    topology: p.topology || 'SPREAD',
    legs: (p.legs || []).map(l => `${l.side === 'BUY' ? '+' : '-'}${l.strike}${l.right || ''}`).join(' / ') || '—',
    expiry: p.expiry || '—',
    dte: p.meta?.dte ?? 0,
    contracts: p.contracts || 0,
    entry_cost: p.entry_cost || 0,
    mtm: p.meta?.mtm ?? p.entry_cost ?? 0,
    pnl: p.realized_pnl ?? 0,
    pnl_pct: p.meta?.pnl_pct ?? 0,
    state: p.state,
    stop: p.meta?.stop ?? -50,
    tp: p.meta?.tp ?? 75,
    trailing: p.meta?.trailing ?? 15,
    entered: p.entry_time ? new Date(p.entry_time) : null,
    hwm: p.high_water_mark ?? 0,
  }));
}

function mergeClosed(res) {
  if (!res?.positions) return [];
  return res.positions
    .filter(p => p.state === 'closed')
    .map(p => ({
      id: p.id,
      symbol: p.symbol,
      legs: (p.legs || []).map(l => `${l.side === 'BUY' ? '+' : '-'}${l.strike}${l.right || ''}`).join(' / ') || '—',
      contracts: p.contracts || 0,
      entry: p.entry_cost || 0,
      exit: p.exit_cost || 0,
      pnl: p.realized_pnl ?? 0,
      pnl_pct: p.meta?.pnl_pct ?? 0,
      reason: p.exit_reason || 'manual',
      closed: p.exit_time ? new Date(p.exit_time) : null,
      held_days: p.meta?.held_days ?? 0,
    }));
}

function mergeOrders(res) {
  if (!res?.orders) return [];
  return res.orders.map((o, i) => ({
    id: o.orderId ? `ord_${o.orderId}` : `ord_${i}`,
    pos: o.permId || null,
    kind: o.action === 'SELL' ? 'exit' : 'entry',
    side: o.action || 'BUY',
    type: o.orderType || 'LMT',
    qty: o.totalQuantity ?? 0,
    lmt: o.lmtPrice ?? 0,
    status: (o.status || 'pending').toLowerCase(),
    submitted: o.time ? new Date(o.time) : new Date(),
    fill: o.avgFillPrice ?? null,
    commission: o.commission ?? 0,
    idem: o.idempotency_key || '—',
  }));
}

function mergeDailyPnl(res) {
  if (!res?.history) return [];
  return res.history.map(d => ({
    date: d.date?.slice(5) ?? '—',
    pnl: d.pnl ?? 0,
    trades: d.trades ?? 0,
  }));
}

function mergeScanner(src, res) {
  if (!res) return src;
  return {
    ...src,
    running: res.active ?? false,
    mode: (res.mode || 'paper').toUpperCase(),
    cadence: res.timing_mode === 'interval'
      ? `every ${res.timing_value ?? 15}s`
      : res.timing_mode || 'idle',
    logs: (res.logs || []).slice(0, 40).map(l => ({
      t: l.time?.slice(11, 19) ?? '—',
      signal: !!l.signal,
      price: l.price ?? 0,
      rsi: l.rsi ?? 0,
      rsi_ok: l.details?.rsi_ok ?? false,
      ema_ok: l.details?.ema_ok ?? true,
      sma200_ok: l.details?.sma200_ok ?? true,
      vol_ok: l.details?.vol_ok ?? true,
      msg: l.msg || '',
    })),
    last_signal: res.logs?.[0]
      ? {
        fire: !!res.logs[0].signal,
        price: res.logs[0].price ?? 0,
        rsi: res.logs[0].rsi ?? 0,
        rsi_ok: res.logs[0].details?.rsi_ok ?? false,
        ema_ok: res.logs[0].details?.ema_ok ?? false,
        sma200_ok: res.logs[0].details?.sma200_ok ?? false,
        vol_ok: res.logs[0].details?.vol_ok ?? false,
        vix_ok: res.logs[0].details?.vix_ok ?? false,
        regime_ok: res.logs[0].details?.regime_ok ?? false,
        time: res.logs[0].time ? new Date(res.logs[0].time) : null,
      }
      : src.last_signal,
  };
}

function mergeEvents(res) {
  if (!res?.events) return [];
  return res.events;
}

function deriveIbkrStatus(hb) {
  if (!hb) return 'off';
  if (hb.ibkr_dropped || hb.alive === false) return 'off';
  if ((hb.status === 'connected' || hb.status === 'online') && hb.alive) return 'live';
  if (hb.status === 'reconnecting' || hb.status === 'warn') return 'warn';
  return 'off';
}

export function DataProvider({ children }) {
  const [data, setData] = useState(MOCK);
  const [online, setOnline] = useState(false);
  const [ibkr, setIbkr] = useState('off');
  const timersRef = useRef([]);

  useEffect(() => {
    let mounted = true;

    const updates = {
      heartbeat: async () => {
        const hb = await safe(api.heartbeat);
        if (!mounted) return;
        setOnline(!!hb);
        setIbkr(deriveIbkrStatus(hb));
        setData(d => ({
          ...d,
          account: mergeAccount(d.account, hb),
          monitor: mergeMonitor(d.monitor, hb),
          risk: mergeRisk(d.risk, hb),
          alerts: hb?.alerts?.length ? mergeAlerts(hb) : d.alerts,
        }));
      },
      scanner: async () => {
        const s = await safe(api.scannerStatus);
        if (!mounted || !s) return;
        setData(d => ({ ...d, scanner: mergeScanner(d.scanner, s) }));
      },
      positions: async () => {
        const open = await safe(api.openPositions);
        const all = await safe(api.allPositions);
        if (!mounted) return;
        setData(d => ({
          ...d,
          positions: mergePositions(d.positions, open) || d.positions,
          closed: all ? mergeClosed(all) : d.closed,
        }));
      },
      pnl: async () => {
        const p = await safe(() => api.dailyPnl(30));
        if (!mounted || !p) return;
        setData(d => ({ ...d, dailyPnl: mergeDailyPnl(p) }));
      },
      orders: async () => {
        const o = await safe(api.orders);
        if (!mounted || !o) return;
        setData(d => ({ ...d, orders: mergeOrders(o) }));
      },
      spy: async () => {
        const s = await safe(() => api.spyIntraday(IBKR_CREDS.host, IBKR_CREDS.port, IBKR_CREDS.client_id));
        if (!mounted || !s) return;
        setData(d => ({ ...d, spy: mergeSpy(d.spy, s) }));
      },
    };

    Object.values(updates).forEach(fn => fn());

    const timers = [
      setInterval(updates.heartbeat, POLL_MS.heartbeat),
      setInterval(updates.scanner,   POLL_MS.scanner),
      setInterval(updates.positions, POLL_MS.positions),
      setInterval(updates.pnl,       POLL_MS.pnl),
      setInterval(updates.orders,    POLL_MS.orders),
      setInterval(updates.spy,       POLL_MS.spy),
    ];
    timersRef.current = timers;

    return () => { mounted = false; timers.forEach(clearInterval); };
  }, []);

  return (
    <DataContext.Provider value={{ ...data, __online: online, __ibkr: ibkr }}>
      {children}
    </DataContext.Provider>
  );
}
