# Trading Platform Wireframes

ASCII mockups derived from `res.jpeg`. These are the layout contracts the
React views must hit (`frontend/src/views/LiveView.jsx`,
`frontend/src/views/BacktestView.jsx`).

---

## Shared header — Calendar Strip

Top of every dashboard. Months row + days row. Selecting a month/day fires
a single `onChange({ year, month, day })` callback that filters the
dashboard's date range.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ JAN  FEB  MAR  APR  MAY  JUN  JUL  AUG  SEP  OCT  NOV  DEC      [year] │
│  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 ... 28 29 30 31  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## LIVE Dashboard

```
╔══════════════════════════════════════════════════════════════════════════╗
║                          CALENDAR  STRIP                                 ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                          ┌──────────────────────────────┐║
║  ┌─────────────────────────────────────┐ │   IBKR                       │║
║  │           Account Info              │ │   Connection                 │║
║  │  • Equity   • Buying Power          │ │   Section                    │║
║  │  • Cash     • Day P&L               │ │   host / port / clientId     │║
║  └─────────────────────────────────────┘ └──────────────────────────────┘║
║                                                                          ║
║  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────────────┐  ║
║  │   Monitor    │ │     KILL     │ │            Alerts                │  ║
║  │  HEARTBEAT   │ │    SWITCH    │ │  · risk breach                   │  ║
║  │  ▮▮▯▮▮▮▯▮   │ │   [ STOP ]   │ │  · order rejected                │  ║
║  └──────────────┘ └──────────────┘ └──────────────────────────────────┘  ║
║                                                                          ║
║  ┌──────────────────────────┐ ┌──────────────────────────────────────┐   ║
║  │  Scanner —               │ │  Scanning Shows                      │   ║
║  │  Load Presets            │ │  (Preset info)                       │   ║
║  │  ▾ select preset         │ │  active preset · last tick · count   │   ║
║  │  [ Start ] [ Stop ]      │ │  filters · symbols · interval        │   ║
║  └──────────────────────────┘ └──────────────────────────────────────┘   ║
║                                                                          ║
║  ┌──────────────────────────┐ ┌──────────────────────────────────────┐   ║
║  │     Signals Log          │ │           Positions                  │   ║
║  │  time · sym · side · qty │ │  sym · side · qty · entry · P&L      │   ║
║  └──────────────────────────┘ └──────────────────────────────────────┘   ║
║                                                                          ║
║  ┌──────────────────────────────────────────────────────────────────┐    ║
║  │                          ORDER  TICKET                            │    ║
║  │  symbol · topology · width · DTE · contracts · est. cost          │    ║
║  │  chain preview                       [ Preview ] [ Submit ]       │    ║
║  └──────────────────────────────────────────────────────────────────┘    ║
╚══════════════════════════════════════════════════════════════════════════╝
```

### Row breakdown

| Row | Cols | Cards                                      |
|-----|------|--------------------------------------------|
| 1   | 2    | Account Info · IBKR Connection             |
| 2   | 3    | Monitor Heartbeat · Kill Switch · Alerts   |
| 3   | 2    | Scanner Load Presets · Scanning Shows      |
| 4   | 2    | Signals Log · Positions                    |
| 5   | 1    | Order Ticket (full width)                  |

---

## BACK-TESTER Dashboard

```
╔══════════════════════════════════════════════════════════════════════════╗
║                          CALENDAR  STRIP                                 ║
╠══════════════════════════════════════════════════════════════════════════╣
║  ┌────────────────────────────────────────────────────────────────────┐  ║
║  │                       HEADER  (KEEP SAME)                          │  ║
║  │  KPIs: Total Return · Sharpe · Max DD · Win Rate · Trades          │  ║
║  └────────────────────────────────────────────────────────────────────┘  ║
║                                                                          ║
║  ┌──────────────┐ ┌──────────────────────────┐ ┌──────────────────┐     ║
║  │  Strategy    │ │                          │ │                  │     ║
║  │  and         │ │   HISTORIC CHRT          │ │     Equity       │     ║
║  │  Capital     │ │   WITH TRADES            │ │     draw         │     ║
║  │              │ │                          │ │     down         │     ║
║  │  • ticker    │ │  price · entries · exits │ │                  │     ║
║  │  • capital   │ │                          │ │   underwater     │     ║
║  │  • sizing    │ │                          │ │   curve          │     ║
║  │  • strategy  │ │                          │ │                  │     ║
║  │  • params    │ │                          │ │                  │     ║
║  ├──────────────┤ │                          │ │                  │     ║
║  │   STATS      │ │                          │ │                  │     ║
║  │  pf · avg    │ │                          │ │                  │     ║
║  │  trades      │ │                          │ │                  │     ║
║  ├──────────────┤ ├──────────────────────────┴─┴──────────────────┤     ║
║  │   FILTERS    │ │              Trades  (List)                    │     ║
║  │  rsi / ema   │ │  date · side · qty · entry · exit · P&L · tag  │     ║
║  │  vix / regime│ │                                                │     ║
║  ├──────────────┤ │                                                │     ║
║  │  Analytics   │ │                                                │     ║
║  │  MC · WF     │ │                                                │     ║
║  │              │ ├──────────────────────────────────┬─────────────┤     ║
║  │              │ │           Analytics              │  OPTIMISER  │     ║
║  │              │ │  monte carlo · walk-forward      │  param x/y  │     ║
║  │              │ │  rolling sharpe · drawdown dist  │  heatmap    │     ║
║  └──────────────┘ └──────────────────────────────────┴─────────────┘     ║
╚══════════════════════════════════════════════════════════════════════════╝
```

### Row breakdown

| Row | Cols   | Cards                                                        |
|-----|--------|--------------------------------------------------------------|
| 1   | 1      | Header KPIs (full width — keep existing 5-col KPI strip)     |
| 2   | 3      | Strategy & Capital · Historic Chart with Trades · Equity DD  |
| 3   | 2      | Stats · (continues chart column above)                       |
| 4   | 2      | Filters · Trades List                                        |
| 5   | 2      | Analytics · Optimiser (heatmap from `OptimiserCard.jsx`)     |

The left rail (Strategy/Capital → Stats → Filters → Analytics) is one
vertical column; the middle is the chart that spans down into the trades
list; the right column is the equity drawdown panel that wraps into the
optimiser heatmap at the bottom.

---

## Component map

| Wireframe block            | React component                                    |
|----------------------------|----------------------------------------------------|
| Calendar Strip             | `frontend/src/calendarStrip.jsx`                   |
| Account Info               | `LiveView.jsx` — IBKR account card                 |
| IBKR Connection            | `LiveView.jsx` — connect form                      |
| Monitor Heartbeat          | `primitives.jsx` → `<Heartbeat>`                   |
| Kill Switch                | `LiveView.jsx` — flatten button                    |
| Alerts                     | `LiveView.jsx` — events feed                       |
| Scanner / Scanning Shows   | `LiveView.jsx` — preset scanner controls           |
| Signals Log                | shared signals table (lifted from `ScannerView`)   |
| Positions                  | `LiveView.jsx` — open positions table              |
| Order Ticket               | `LiveView.jsx` — chain preview + submit            |
| Header KPIs                | `BacktestView.jsx` — 5-col KPI grid                |
| Strategy & Capital         | `BacktestView.jsx` + `strategyParamsForm.jsx`      |
| Historic Chart with Trades | `chart.jsx` → `<EquityChart>` / price overlay      |
| Equity Drawdown            | drawdown chart in `BacktestView.jsx`               |
| Stats                      | `BacktestView.jsx` — Avg/PF/Total cluster          |
| Filters                    | `BacktestView.jsx` — Filters & Analytics card      |
| Trades List                | journal trades table                               |
| Analytics                  | MC histogram + walk-forward                        |
| Optimiser                  | `frontend/src/optimiserCard.jsx`                   |
