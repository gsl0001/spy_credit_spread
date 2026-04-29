# SPY ORB — NautilusTrader + moomoo

Standalone live-trading system running the SPY 0DTE ORB strategy
(SSRN 6355218) through moomoo OpenD instead of IBKR TWS.

Uses [NautilusTrader](https://nautilustrader.io) (Rust-core event engine) and
[nautilus-futu](https://github.com/nautechsystems/nautilus-futu) (Futu TCP
adapter) extended with options support.

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12+ | Isolated from the parent project's Python |
| moomoo OpenD v8.3+ | Download from moomoo developer portal; must be running on FUTU_HOST:FUTU_PORT |
| Rust toolchain | Only needed if installing `nautilus-futu` from source (no pre-built wheel) |

## Installation

```bash
cd nautilus/
pip install -e ".[dev]"
```

## Configuration

Copy the parent project's `config/.env.example` to `config/.env` and fill in the
`FUTU_*` variables (the nautilus sub-project reads from that same file):

```
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_TRD_ENV=0            # 0=simulate, 1=real
FUTU_UNLOCK_PWD_MD5=      # echo -n "your_pin" | md5sum
FUTU_ACC_ID=0             # 0 = auto-discover first US margin account
FUTU_LEG_FILL_TIMEOUT_S=30
```

## Running live

```bash
cd nautilus/
python run_live.py
```

Stop with `Ctrl+C` — on_stop() submits market close orders for any open position.

## Running backtest

```bash
cd nautilus/
python run_backtest.py --start 2026-01-01 --end 2026-04-25
```

Loads SPY 5-min bars from yfinance. Option chain data is not available in
backtest mode — spread submissions are logged as "no instruments found" but all
entry/exit filters (day of week, VIX, OR range, news days) are fully exercised.

## Running tests

```bash
cd nautilus/
pytest tests/ -v
```

## Directory structure

```
nautilus/
├── pyproject.toml
├── README.md
├── run_live.py                  Entry point (TradingNode)
├── run_backtest.py              Entry point (BacktestEngine)
├── config/
│   ├── settings.py              Reads FUTU_* from config/.env
│   └── live_config.py           Builds TradingNodeConfig
├── adapters/
│   └── futu_options/
│       ├── config.py            NautilusConfig dataclasses
│       ├── providers.py         FutuOptionsInstrumentProvider
│       ├── data.py              FutuOptionsDataClient (bars + quotes + VIX)
│       ├── execution.py         FutuOptionsExecClient (legged spread execution)
│       └── factories.py         LiveDataClientFactory / LiveExecClientFactory
├── strategies/
│   └── orb_spread.py            OrbSpreadStrategy + OrbSpreadConfig
└── tests/
    ├── test_orb_strategy.py     Strategy logic unit tests
    └── test_instruments.py      Instrument provider unit tests
```

## Known constraints

- **No atomic spread orders** — moomoo has no combo order API. Spreads are
  placed as 2 sequential legs. If leg 2 times out, leg 1 is market-sold.
- **`nautilus-futu` is a community package** — adapter layer is isolated in
  `adapters/futu_options/` so it can be replaced if the package is abandoned.
- **VIX via Futu** — `get_market_snapshot(["US.VIX"])` refreshed every 5 min.
  Fail-closed: if unavailable, no new positions are opened.
- **Events file expires** — `config/events_2026.json` covers 2026 only.
  Update or extend before end of 2026.
