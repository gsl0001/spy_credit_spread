import { Ico } from './icons.jsx';

// NOTE: visibility is controlled by the parent (App.jsx renders this only
// when `tweaksOpen` is true). The previous `if (!open) return null` guard
// expected an `open` prop the parent never passed, so the panel never
// rendered. Mount/unmount stays the parent's responsibility.
export function TweaksPanel({ onClose, tweaks, setTweaks }) {
  const set = (k, v) => setTweaks(prev => ({ ...prev, [k]: v }));

  return (
    <div className="tweaks">
      <div className="tweaks__head">
        <span>Tweaks</span>
        <button className="btn ghost icon" onClick={onClose}><Ico name="x" size={14} /></button>
      </div>
      <div className="tweaks__body">
        <div className="tweaks__row">
          <label>Accent</label>
          <div className="swatches">
            {[
              ['emerald', 'oklch(0.78 0.16 155)'],
              ['cyan',    'oklch(0.78 0.12 220)'],
              ['amber',   'oklch(0.82 0.14 85)'],
              ['violet',  'oklch(0.72 0.16 300)'],
            ].map(([k, c]) => (
              <button key={k} className="sw" style={{ background: c }} aria-pressed={tweaks.accent === k} onClick={() => set('accent', k)} />
            ))}
          </div>
        </div>
        <div className="tweaks__row">
          <label>Density</label>
          <div className="seg">
            {['compact', 'balanced', 'airy'].map(d => (
              <button key={d} aria-pressed={tweaks.density === d} onClick={() => set('density', d)}>{d}</button>
            ))}
          </div>
        </div>
        <div className="tweaks__row">
          <label>Theme</label>
          <div className="seg">
            {['dark', 'darker', 'light'].map(t => (
              <button key={t} aria-pressed={tweaks.theme === t} onClick={() => set('theme', t)}>{t}</button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
