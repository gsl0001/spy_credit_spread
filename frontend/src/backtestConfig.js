export const STORAGE_KEY = 'spy_backtest_config';
export const PRESETS_KEY = 'spy_backtest_presets';

export const DEFAULT_CONFIG = {
  ticker: 'SPY',
  years_history: 2,
  capital_allocation: 10000,
  use_dynamic_sizing: false,
  risk_percent: 5,
  max_trade_cap: 0,
  contracts_per_trade: 1,
  spread_cost_target: 250,
  strategy_id: 'consecutive_days',
  strategy_type: 'bull_call',
  topology: 'vertical_spread',
  direction: 'bull',
  strike_width: 5,
  take_profit_pct: 0,
  trailing_stop_pct: 0,
  target_dte: 14,
  stop_loss_pct: 50,
  commission_per_contract: 0.65,
  use_rsi_filter: true,
  rsi_threshold: 30,
  use_ema_filter: true,
  ema_length: 10,
  use_sma200_filter: false,
  use_volume_filter: false,
  use_mark_to_market: true,
  enable_mc_histogram: false,
  enable_walk_forward: false,
  walk_forward_windows: 4,
  use_vix_filter: false,
  vix_min: 15,
  vix_max: 35,
  use_regime_filter: false,
  regime_allowed: 'all',
  entry_red_days: 2,
  exit_green_days: 1,
};

export const BUILT_IN_PRESETS = {
  Conservative: {
    ...DEFAULT_CONFIG,
    entry_red_days: 3, target_dte: 21, stop_loss_pct: 30, spread_cost_target: 150,
    use_rsi_filter: true, rsi_threshold: 25,
    use_regime_filter: true, regime_allowed: 'bull',
  },
  Aggressive: {
    ...DEFAULT_CONFIG,
    entry_red_days: 1, target_dte: 7, stop_loss_pct: 75, spread_cost_target: 400,
    use_rsi_filter: false, use_ema_filter: false, contracts_per_trade: 3,
  },
  'Post-Crash Recovery': {
    ...DEFAULT_CONFIG,
    entry_red_days: 4, target_dte: 30, stop_loss_pct: 50,
    use_vix_filter: true, vix_min: 25, vix_max: 60,
    use_rsi_filter: true, rsi_threshold: 20,
  },
  'Low-Vol Scalp': {
    ...DEFAULT_CONFIG,
    entry_red_days: 2, target_dte: 7, stop_loss_pct: 40, spread_cost_target: 100,
    use_vix_filter: true, vix_min: 10, vix_max: 20,
  },
  'Bear Market': {
    ...DEFAULT_CONFIG,
    strategy_type: 'bear_put', direction: 'bear',
    entry_red_days: 2, target_dte: 14,
    use_regime_filter: true, regime_allowed: 'bear',
  },
};

export function loadConfig() {
  try {
    const s = localStorage.getItem(STORAGE_KEY);
    if (s) return { ...DEFAULT_CONFIG, ...JSON.parse(s) };
  } catch (_) {}
  return { ...DEFAULT_CONFIG };
}

export function saveConfig(cfg) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg)); } catch (_) {}
}

export function loadPresets() {
  try {
    const s = localStorage.getItem(PRESETS_KEY);
    if (s) return JSON.parse(s);
  } catch (_) {}
  return {};
}

export function savePresets(presets) {
  try { localStorage.setItem(PRESETS_KEY, JSON.stringify(presets)); } catch (_) {}
}
