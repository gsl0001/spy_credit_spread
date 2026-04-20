import requests

data = {
  "ticker": "SPY",
  "years_history": 2,
  "capital_allocation": 10000.0,
  "use_dynamic_sizing": False,
  "risk_percent": 5.0,
  "max_trade_cap": 0.0,
  "contracts_per_trade": 1,
  "spread_cost_target": 250.0,
  "entry_red_days": 2,
  "exit_green_days": 2,
  "target_dte": 14,
  "stop_loss_pct": 50,
  "use_rsi_filter": True,
  "rsi_threshold": 30,
  "use_ema_filter": True,
  "ema_length": 10,
  "use_sma200_filter": False,
  "use_volume_filter": False
}

try:
    res = requests.post("http://127.0.0.1:8000/api/backtest", json=data)
    print(res.status_code)
    out = res.json()
    if "error" in out:
        print("ERROR:", out["error"])
    else:
        print("Trades count:", len(out.get("trades", [])))
        print("First trade:", out.get("trades", [])[0] if len(out.get("trades", [])) > 0 else "None")
except Exception as e:
    print("FAILED:", e)
