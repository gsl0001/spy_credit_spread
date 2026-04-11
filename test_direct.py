from main import backtest, BacktestRequest

def test_strategy(strategy_id):
    print(f"\n--- Testing Strategy: {strategy_id} ---")
    req = BacktestRequest(
        ticker="SPY",
        years_history=2,
        capital_allocation=10000.0,
        strategy_id=strategy_id,
        contracts_per_trade=1,
        spread_cost_target=250.0,
        target_dte=14,
        stop_loss_pct=50
    )

    try:
        out = backtest(req)
        if "error" in out:
            print("ERROR:", out["error"])
        else:
            print("Trades count:", len(out.get("trades", [])))
            print("Total PnL:", out["metrics"]["total_pnl"])
            if len(out.get("trades", [])) > 0:
                print("First trade reason:", out["trades"][0].get("reason", "manual/streak"))
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_strategy("consecutive_days")
    test_strategy("combo_spread")
