import { useMemo } from 'react';

/**
 * Calendar strip per res.jpeg wireframe.
 *
 * Top row: months JAN..DEC for the active year.
 * Bottom row: days 1..31 of the active month.
 *
 * Props:
 *   year:           number (default = current year)
 *   selectedMonth:  0..11 (default = current month)
 *   selectedDay:    1..31 | null (default = today if month matches, else null)
 *   onChange:       ({ year, month, day }) => void  (day may be null)
 */
const MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];

function daysInMonth(year, monthIdx) {
  return new Date(year, monthIdx + 1, 0).getDate();
}

export function CalendarStrip({
  year,
  selectedMonth,
  selectedDay = null,
  onChange,
}) {
  const today = useMemo(() => new Date(), []);
  const y = year ?? today.getFullYear();
  const m = selectedMonth ?? today.getMonth();
  const d = selectedDay;
  const days = useMemo(() => {
    const n = daysInMonth(y, m);
    return Array.from({ length: n }, (_, i) => i + 1);
  }, [y, m]);

  const isTodayMonth = y === today.getFullYear() && m === today.getMonth();
  const todayDay = today.getDate();

  const fire = (next) => onChange && onChange({ year: y, month: m, day: d, ...next });

  const cellBase = {
    padding: '4px 8px',
    fontSize: 11,
    cursor: 'pointer',
    border: '1px solid var(--border)',
    background: 'var(--bg-2)',
    color: 'var(--text-2)',
    borderRadius: 4,
    minWidth: 28,
    textAlign: 'center',
    userSelect: 'none',
  };
  const activeStyle = {
    background: 'var(--accent, #3b82f6)',
    color: '#fff',
    borderColor: 'var(--accent, #3b82f6)',
  };
  const todayStyle = {
    outline: '1px solid var(--accent, #3b82f6)',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 }}>
      <div style={{ display: 'flex', gap: 4, overflowX: 'auto', paddingBottom: 2 }}>
        {MONTHS.map((mn, idx) => {
          const selected = idx === m;
          return (
            <div key={mn}
                 onClick={() => fire({ month: idx, day: null })}
                 style={{ ...cellBase, ...(selected ? activeStyle : {}), fontWeight: selected ? 700 : 500 }}>
              {mn}
            </div>
          );
        })}
        <div style={{ marginLeft: 'auto', alignSelf: 'center', fontSize: 11, color: 'var(--text-3)' }}>{y}</div>
      </div>
      <div style={{ display: 'flex', gap: 3, overflowX: 'auto' }}>
        {days.map(dn => {
          const selected = dn === d;
          const isToday = isTodayMonth && dn === todayDay;
          return (
            <div key={dn}
                 onClick={() => fire({ day: dn })}
                 style={{
                   ...cellBase,
                   ...(selected ? activeStyle : {}),
                   ...(isToday && !selected ? todayStyle : {}),
                   minWidth: 24,
                   padding: '3px 6px',
                 }}>
              {dn}
            </div>
          );
        })}
      </div>
    </div>
  );
}
