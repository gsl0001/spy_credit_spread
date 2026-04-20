import { useState, useEffect } from 'react';
import { Sidebar } from './sidebar.jsx';
import { Topbar } from './topbar.jsx';
import { TweaksPanel } from './tweaks.jsx';
import { LiveView } from './views/LiveView.jsx';
import { JournalView } from './views/JournalView.jsx';
import { ScannerView } from './views/ScannerView.jsx';
import { RiskView } from './views/RiskView.jsx';
import { BacktestView } from './views/BacktestView.jsx';
import { PaperView } from './views/PaperView.jsx';
import { Ico } from './icons.jsx';
import { fmtTimeAgo, Btn } from './primitives.jsx';
import { useData } from './useBackendData.jsx';

const STORAGE_VIEW = 'spy_ui_view';

const VIEWS = {
  live: LiveView,
  paper: PaperView,
  backtest: BacktestView,
  journal: JournalView,
  risk: RiskView,
  scanner: ScannerView,
};

const VIEW_TITLES = {
  live: { title: 'Live Trading', sub: 'IBKR TWS · SPY credit spreads' },
  paper: { title: 'Paper Trading', sub: 'Alpaca · equity surrogate' },
  backtest: { title: 'Backtest Engine', sub: 'historical simulation' },
  journal: { title: 'Trade Journal', sub: 'orders · fills · events' },
  risk: { title: 'Risk & Guardrails', sub: 'pre-trade gates · sizing · events' },
  scanner: { title: 'Signal Scanner', sub: 'RSI · EMA · regime filters' },
};

export default function App() {
  const [view, setView] = useState(() => localStorage.getItem(STORAGE_VIEW) || 'live');
  const [tweaks, setTweaks] = useState({ accent: 'emerald', density: 'balanced', theme: 'dark' });
  const [tweaksOpen, setTweaksOpen] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  const [panicOpen, setPanicOpen] = useState(false);
  const backend = useData();

  useEffect(() => { localStorage.setItem(STORAGE_VIEW, view); }, [view]);

  useEffect(() => {
    document.documentElement.setAttribute('data-accent', tweaks.accent);
    document.documentElement.setAttribute('data-density', tweaks.density);
    document.documentElement.setAttribute('data-theme', tweaks.theme);
  }, [tweaks]);

  useEffect(() => {
    const h = () => setTweaksOpen(true);
    window.addEventListener('open-tweaks', h);
    return () => window.removeEventListener('open-tweaks', h);
  }, []);

  const ViewComponent = VIEWS[view] || LiveView;
  const { title, sub } = VIEW_TITLES[view] || VIEW_TITLES.live;
  const alertCount = backend.alerts.filter(a => !a.ack).length;

  return (
    <div className="shell">
      <Sidebar view={view} setView={setView} alertCount={alertCount} onTweaks={() => setTweaksOpen(true)} />

      <Topbar
        view={view}
        mkt={{ open: backend.risk.market_open, next: backend.risk.next_close || '—' }}
        spy={backend.spy}
        conn={{ ibkr: backend.__online ? backend.__ibkr : 'off' }}
        leader={{ is_leader: backend.monitor.is_leader }}
        alertCount={alertCount}
        onBell={() => setBellOpen(b => !b)}
        onPanic={() => setPanicOpen(true)}
      />

      <main className="main">
        <ViewComponent />
      </main>

      {bellOpen && (
        <div style={{
          position: 'fixed', top: 56, right: 12, zIndex: 200,
          background: 'var(--bg-1)', border: '1px solid var(--border)', borderRadius: 10,
          boxShadow: '0 8px 32px rgba(0,0,0,.4)', width: 340, maxHeight: 480, overflowY: 'auto',
        }} onClick={e => e.stopPropagation()}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>Alerts</span>
            <button onClick={() => setBellOpen(false)} style={{ background: 'none', border: 'none', color: 'var(--text-3)', cursor: 'pointer', padding: 4 }}>
              <Ico name="x" size={14} />
            </button>
          </div>
          {backend.alerts.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>No alerts</div>
          ) : backend.alerts.map((a, i) => (
            <div key={i} className={`alert alert--${a.level}`} style={{ margin: '8px 12px', borderRadius: 6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 2 }}>{a.title}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-2)' }}>{a.body}</div>
                </div>
                <span style={{ fontSize: 10, color: 'var(--text-3)', whiteSpace: 'nowrap' }}>{fmtTimeAgo(a.ts)}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {bellOpen && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 199 }} onClick={() => setBellOpen(false)} />
      )}

      {panicOpen && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 300, background: 'rgba(0,0,0,.6)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setPanicOpen(false)}>
          <div style={{
            background: 'var(--bg-1)', border: '1px solid var(--border)', borderRadius: 12,
            padding: '32px 40px', width: 420, textAlign: 'center',
            boxShadow: '0 16px 64px rgba(0,0,0,.6)',
          }} onClick={e => e.stopPropagation()}>
            <div style={{ width: 56, height: 56, borderRadius: '50%', background: 'oklch(38% .18 20)', border: '2px solid oklch(52% .22 20)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
              <Ico name="alert-triangle" size={28} stroke={2} />
            </div>
            <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 8 }}>Flatten All Positions?</div>
            <div style={{ color: 'var(--text-2)', fontSize: 13, marginBottom: 24, lineHeight: 1.5 }}>
              This will immediately submit market orders to close all {backend.positions.length} open spread(s). Action cannot be undone.
            </div>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
              <Btn variant="ghost" onClick={() => setPanicOpen(false)}>Cancel</Btn>
              <Btn variant="danger" icon="zap" onClick={() => setPanicOpen(false)}>Confirm Flatten All</Btn>
            </div>
          </div>
        </div>
      )}

      {tweaksOpen && (
        <TweaksPanel tweaks={tweaks} setTweaks={setTweaks} onClose={() => setTweaksOpen(false)} />
      )}
    </div>
  );
}
