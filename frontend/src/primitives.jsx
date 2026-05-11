/* eslint-disable react-refresh/only-export-components */
export const cls = (...xs) => xs.filter(Boolean).join(' ');

export const fmtUsd = (n, sign = false) => {
  const v = Number(n || 0);
  const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (sign && v > 0 ? '+' : v < 0 ? '−' : '') + '$' + s;
};

export const fmtPct = (n, digits = 2) => {
  const v = Number(n || 0);
  return (v > 0 ? '+' : '') + v.toFixed(digits) + '%';
};

export const fmtTimeAgo = (d) => {
  if (!d) return '—';
  const now = new Date(), dt = new Date(d);
  const s = Math.floor((now - dt) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
};

export const fmtTimeHMS = (d) => d ? new Date(d).toTimeString().slice(0, 8) : '—';

export function Card({ title, actions, children, flush, icon, subtitle, style }) {
  return (
    <div className="card" style={style}>
      {title && (
        <div className="card__head">
          <span className="title">
            {icon && <IcoInline name={icon} size={13} />} {title}
            {subtitle && <span className="muted" style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 500, marginLeft: 6 }}>{subtitle}</span>}
          </span>
          {actions && <div className="card__actions">{actions}</div>}
        </div>
      )}
      <div className={cls('card__body', flush && 'card__body--flush')}>{children}</div>
    </div>
  );
}

function IcoInline({ name, size = 16, stroke = 2 }) {
  const props = {
    width: size, height: size, viewBox: "0 0 24 24",
    fill: "none", stroke: "currentColor", strokeWidth: stroke,
    strokeLinecap: "round", strokeLinejoin: "round",
  };
  const paths = {
    activity: <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>,
    dashboard: <g><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></g>,
    zap: <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>,
    book: <g><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></g>,
    shield: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>,
    radar: <g><circle cx="12" cy="12" r="10"/><path d="M12 12 7 7"/><circle cx="12" cy="12" r="4"/></g>,
    bell: <g><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></g>,
    cog: <g><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></g>,
    sliders: <g><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></g>,
    check: <polyline points="20 6 9 17 4 12"/>,
    info: <g><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></g>,
    x: <g><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></g>,
    trending: <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/>,
    send: <g><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></g>,
    target: <g><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></g>,
    calendar: <g><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></g>,
    wifi: <g><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></g>,
    clock: <g><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></g>,
    layers: <g><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></g>,
  };
  return <svg {...props}>{paths[name] || null}</svg>;
}

export function Kpi({ label, value, delta, color, big, suffix }) {
  return (
    <div className={cls('kpi', big && 'kpi--lg')}>
      <span className="kpi__label">{label}</span>
      <span className="kpi__value" style={color ? { color } : null}>
        {value}{suffix && <span style={{ fontSize: '0.6em', color: 'var(--text-3)', marginLeft: 4 }}>{suffix}</span>}
      </span>
      {delta && <span className="kpi__delta" style={delta.color ? { color: delta.color } : null}>{delta.text}</span>}
    </div>
  );
}

export function Badge({ variant = 'neutral', children, dot }) {
  return <span className={cls('badge', variant, dot && 'dot')}>{children}</span>;
}

export function Btn({ variant, size, icon, children, iconOnly, ...rest }) {
  return (
    <button className={cls('btn', variant, size, iconOnly && 'icon')} {...rest}>
      {icon && <IcoInline name={icon} size={13} />}{children}
    </button>
  );
}

export function Switch({ on, onChange }) {
  return <div className="switch" data-on={!!on} onClick={() => onChange && onChange(!on)} />;
}

export function Sparkline({ data, color = 'var(--accent)', height = 28, width = 120, fill = true }) {
  if (!data || data.length < 2) return null;
  const mn = Math.min(...data), mx = Math.max(...data), range = mx - mn || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * width},${height - ((v - mn) / range) * height}`);
  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ width, height }}>
      {fill && <polygon points={`0,${height} ${pts.join(' ')} ${width},${height}`} fill={color} opacity="0.08" />}
      <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth="1.3" />
    </svg>
  );
}

export function Progress({ value, max = 100, variant }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  return <div className={cls('pbar', variant)}><span style={{ width: pct + '%' }} /></div>;
}

export function Pill({ kind = 'off', children }) {
  return <span className={cls('conn-pill', kind)}><span className="dot" />{children}</span>;
}

export function RiskBar({ label, used, limit, unit = '%', variant }) {
  const pct = limit ? (used / limit) * 100 : 0;
  const v = variant || (pct > 80 ? 'neg' : pct > 60 ? 'warn' : '');
  return (
    <div className="risk-bar">
      <div className="rh"><span>{label}</span><span><strong>{used}</strong>{unit} / {limit}{unit}</span></div>
      <Progress value={used} max={limit} variant={v} />
    </div>
  );
}

export function Heartbeat({ history }) {
  return (
    <div className="hb">
      {history.map((h, i) => (
        <div key={i} className={`hb__tick ${h.state}`} style={{ height: h.state === 'fail' ? 20 : h.state === 'stale' ? 14 : 10 }} />
      ))}
    </div>
  );
}

export function Chip({ ok, children }) {
  return <span className={cls('chip', ok === true && 'ok', ok === false && 'no')}>{children}</span>;
}

export function Empty({ icon, title, hint, children }) {
  return (
    <div style={{ padding: '32px 16px', textAlign: 'center', color: 'var(--text-3)' }}>
      {icon && <div style={{ opacity: 0.5, marginBottom: 8 }}><IcoInline name={icon} size={28} /></div>}
      {title && <div style={{ fontWeight: 600, color: 'var(--text-2)', marginBottom: 4 }}>{title}</div>}
      {hint && <div style={{ fontSize: 11 }}>{hint}</div>}
      {children && <div style={{ marginTop: 10 }}>{children}</div>}
    </div>
  );
}
