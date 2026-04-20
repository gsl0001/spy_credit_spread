import { Ico } from './icons.jsx';

export function Sidebar({ view, setView, alertCount }) {
  const items = [
    { id: 'live',     icon: 'dashboard', label: 'Live Trading' },
    { id: 'paper',    icon: 'radar',     label: 'Paper (Alpaca)' },
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
          >
            <Ico name={it.icon} size={18} />
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
