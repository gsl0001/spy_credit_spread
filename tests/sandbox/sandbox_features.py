from main import backtest, BacktestRequest

req = BacktestRequest(strategy_type="bear_put", enable_mc_histogram=True, enable_walk_forward=True, walk_forward_windows=3)
result = backtest(req)
print("=== Bear Put Spread ===")
print(f"Trades: {len(result['trades'])}")
print(f"MC distribution bins: {len(result['monte_carlo']['distribution'])}")
print(f"Walk-forward windows: {len(result['walk_forward'])}")
if result["walk_forward"]:
    for w in result["walk_forward"]:
        print(f"  W{w['window']}: {w['start_date']} -> {w['end_date']} | {w['trades']} trades | PnL: {w['pnl']}")
print(f"Metrics: profit_factor={result['metrics']['profit_factor']}, max_consec={result['metrics']['max_consec_losses']}, recovery={result['metrics']['recovery_factor']}")

# Test optimizer
from main import optimize, OptimizerRequest
opt_req = OptimizerRequest(x_values=[1,2,3], y_values=[7,14,21])
opt_result = optimize(opt_req)
print(f"\n=== Optimizer ===")
print(f"Grid cells: {len(opt_result['results'])}")
for r in opt_result["results"][:3]:
    print(f"  {opt_req.param_x}={r['x']}, {opt_req.param_y}={r['y']} -> PnL: {r['pnl']}, WR: {r['win_rate']}%")
