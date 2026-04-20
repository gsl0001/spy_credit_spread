const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

export const IBKR_CREDS = {
  host: localStorage.getItem('ibkr_host') || '127.0.0.1',
  port: Number(localStorage.getItem('ibkr_port')) || 7497,
  client_id: Number(localStorage.getItem('ibkr_client_id')) || 1,
};

export function setIbkrCreds({ host, port, client_id }) {
  if (host != null) { IBKR_CREDS.host = host; localStorage.setItem('ibkr_host', host); }
  if (port != null) { IBKR_CREDS.port = Number(port); localStorage.setItem('ibkr_port', String(port)); }
  if (client_id != null) { IBKR_CREDS.client_id = Number(client_id); localStorage.setItem('ibkr_client_id', String(client_id)); }
}

async function get(path, timeoutMs = 8000) {
  const res = await fetch(`${API}${path}`, { signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

async function post(path, body, timeoutMs = 30000) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

export const api = {
  // Scanner
  scannerStatus:  ()     => get('/api/scanner/status'),
  scannerStart:   (cfg)  => post('/api/scanner/start', cfg),
  scannerStop:    ()     => post('/api/scanner/stop'),

  // Journal / reporting
  openPositions:  ()     => get('/api/journal/positions?state=open'),
  allPositions:   ()     => get('/api/journal/positions?state=all'),
  dailyPnl:       (days = 30) => get(`/api/journal/daily_pnl?days=${days}`),
  events:         (limit = 50) => get(`/api/journal/events?limit=${limit}`),
  reconciliation: ()     => get('/api/journal/reconciliation'),

  // Market data
  spyIntraday:    ()     => get('/api/spy/intraday'),
  liveChain:      (t = 'SPY') => get(`/api/live_chain?ticker=${t}`),
  strategies:     ()     => get('/api/strategies'),

  // IBKR
  heartbeat:      ()     => post('/api/ibkr/heartbeat', IBKR_CREDS),
  ibkrConnect:    ()     => post('/api/ibkr/connect', IBKR_CREDS),
  ibkrPositions:  ()     => post('/api/ibkr/positions', IBKR_CREDS),
  ibkrExecute:    (payload) => post('/api/ibkr/execute', { creds: { ...IBKR_CREDS }, ...payload }, 60000),
  orders:         ()     => get(`/api/ibkr/orders?host=${IBKR_CREDS.host}&port=${IBKR_CREDS.port}&client_id=${IBKR_CREDS.client_id}`),
  flatten:        ()     => post('/api/ibkr/flatten_all', IBKR_CREDS, 60000),
  reconnect:      ()     => post('/api/ibkr/reconnect', IBKR_CREDS),
  ibkrCancel:     (orderId) => post('/api/ibkr/cancel', { ...IBKR_CREDS, orderId }),

  // Paper (Alpaca)
  paperConnect:   (creds) => post('/api/paper/connect', creds),
  paperPositions: (creds) => post('/api/paper/positions', creds),
  paperOrders:    (creds) => post('/api/paper/orders', creds),
  paperExecute:   (payload) => post('/api/paper/execute', payload),
  paperScan:      (payload) => post('/api/paper/scan', payload),

  // Backtest / optimizer
  runBacktest:    (cfg = {}) => post('/api/backtest', cfg, 120000),
  runOptimize:    (payload)  => post('/api/optimize', payload, 300000),
  strategySchema: (id)       => get(`/api/strategies/${id}/schema`),

  // Presets (use_request §4)
  presetsList:    ()         => get('/api/presets'),
  presetGet:      (name)     => get(`/api/presets/${encodeURIComponent(name)}`),
  presetSave:     (preset)   => post('/api/presets', preset),
  presetDelete:   (name)     => fetch(`${API}/api/presets/${encodeURIComponent(name)}`,
                                       { method: 'DELETE' }).then(r => r.json()),

  // Preset-driven scanner
  presetScannerStart:  (name)  => post('/api/scanner/preset/start', { name }),
  presetScannerTick:   ()      => post('/api/scanner/preset/tick'),
  presetScannerStop:   ()      => post('/api/scanner/preset/stop'),
  presetScannerStatus: ()      => get('/api/scanner/preset/status'),
};

export async function safe(fn, fallback = null) {
  try { return await fn(); }
  catch (_e) { return fallback; }
}
