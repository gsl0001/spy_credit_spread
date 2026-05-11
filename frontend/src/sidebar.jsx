import { Ico } from './icons.jsx';

export function Sidebar({ view, setView, alertCount }) {
  const items = [
    { id: 'live',     icon: 'dashboard', label: 'Live Trading' },
    { id: 'paper',    icon: 'radar',     label: 'Paper Trials' },
    { id: 'moomoo',   icon: 'zap',       label: 'Moomoo',        orange: true },
    { id: 'backtest', icon: 'activity',  label: 'Backtest' },
    { id: 'journal',  icon: 'book',      label: 'Journal' },
    { id: 'risk',     icon: 'shield',    label: 'Risk & Guardrails' },
    { id: 'scanner',  icon: 'target',    label: 'Scanner' },
  ];
  return (
    <aside className="rail">
      <div className="rail__logo">S</div>
      <nav className="rail__items">
        {items.map(it => (
          <button
            key={it.id}
            className="rail__btn"
            aria-current={view === it.id ? 'page' : null}
            onClick={() => setView(it.id)}
            style={it.orange && view === it.id ? { color: '#f97316' } : undefined}
          >
            <Ico name={it.icon} size={18} style={it.orange ? { color: '#f97316' } : undefined} />
            {it.id === 'live' && alertCount > 0 && <span className="rail__badge" />}
            <span className="rail__tip">{it.label}</span>
          </button>
        ))}
      </nav>
      <button className="rail__btn" onClick={() => window.dispatchEvent(new CustomEvent('open-tweaks'))}>
        <Ico name="sliders" size={18} />
        <span className="rail__tip">Tweaks</span>
      </button>
      <button className="rail__btn">
        <Ico name="cog" size={18} />
        <span className="rail__tip">Settings</span>
      </button>
    </aside>
  );
}
