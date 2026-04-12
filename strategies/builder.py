import numpy as np
import scipy.stats as si
from typing import List, Dict, Optional

# --- Black-Scholes Utility Functions ---
def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0: return max(0.0, S - K)
    if sigma <= 0: return max(0.0, S - K * np.exp(-r * T))
    # Add robust tiny protections to avoid division by zero
    T = max(T, 1e-5)
    sigma = max(sigma, 1e-4)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * si.norm.cdf(d1) - K * np.exp(-r * T) * si.norm.cdf(d2)

def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0: return max(0.0, K - S)
    if sigma <= 0: return max(0.0, K * np.exp(-r * T) - S)
    return bs_call_price(S, K, T, r, sigma) - S + K * np.exp(-r * T)

# --- Topology Builder Engine ---
class OptionTopologyBuilder:
    """
    Constructs multi-leg option topologies dynamically, returning the strikes and net cost/credit.
    """
    @staticmethod
    def construct_legs(
        topology: str, 
        direction: str, 
        S: float, 
        T: float, 
        r: float, 
        sigma: float, 
        target_cost: float,
        strike_width: int = 5,
        realism_factor: float = 1.15, # Account for IV/HV spread
        target_delta: Optional[float] = None
    ) -> Dict:
        """
        Builds option structures.
        Returns a dict describing the strikes and net entry price.
        Positive price implies a net debit paid.
        Negative price implies a net credit received.
        """
        res = {
            "topology": topology,
            "legs": [], # list of dicts: {"type": "call/put", "strike": K, "side": "long/short", "price": p}
            "net_cost": 0.0,
            "margin_req": 0.0
        }
        
        # Adaptive rounding: SPY -> 1.0, AMZN -> 1.0 (or 0.5), AAPL -> 1.0
        # For high price stocks like NVDA/GOOG (post-splits they are lower), but we use 1.0 as safe default.
        S_round = round(S)
        sigma_adj = sigma * realism_factor
        
        if topology == "long_call":  # Single leg directional
            # Auto-swap to long put if a bearish bias was requested.
            if direction == "bear":
                P_price = bs_put_price(S, S_round, T, r, sigma_adj)
                res["legs"].append({"type": "put", "strike": S_round, "side": "long", "price": P_price})
                res["net_cost"] = P_price * 100
            else:
                C_price = bs_call_price(S, S_round, T, r, sigma_adj)
                res["legs"].append({"type": "call", "strike": S_round, "side": "long", "price": C_price})
                res["net_cost"] = C_price * 100
            res["margin_req"] = res["net_cost"]

        elif topology == "long_put":  # Single leg directional
            P_price = bs_put_price(S, S_round, T, r, sigma_adj)
            res["legs"].append({"type": "put", "strike": S_round, "side": "long", "price": P_price})
            res["net_cost"] = P_price * 100
            res["margin_req"] = res["net_cost"]

        elif topology == "vertical_spread":
            tgt = target_cost / 100.0 if target_cost > 0 else 2.50
            if direction == "bull_call":
                K_long = S_round
                c_long = bs_call_price(S, K_long, T, r, sigma_adj)
                best_K_short, best_diff, c_short_best = K_long + 5, float('inf'), 0.0
                
                for ks in range(K_long + 1, K_long + 41):
                    c_short = bs_call_price(S, ks, T, r, sigma_adj)
                    c = c_long - c_short
                    if c < 0: break
                    d = abs(c - tgt)
                    if d < best_diff: 
                        best_diff, best_K_short, c_short_best = d, ks, c_short
                
                res["legs"] = [
                    {"type": "call", "strike": K_long, "side": "long", "price": c_long},
                    {"type": "call", "strike": best_K_short, "side": "short", "price": c_short_best}
                ]
                res["net_cost"] = (c_long - c_short_best) * 100
                res["margin_req"] = res["net_cost"]

            elif direction == "bear_put":
                K_long = S_round
                p_long = bs_put_price(S, K_long, T, r, sigma_adj)
                best_K_short, best_diff, p_short_best = K_long - 5, float('inf'), 0.0
                
                for ks in range(K_long - 1, max(K_long - 41, 1), -1):
                    p_short = bs_put_price(S, ks, T, r, sigma_adj)
                    c = p_long - p_short
                    if c < 0: break
                    d = abs(c - tgt)
                    if d < best_diff: 
                        best_diff, best_K_short, p_short_best = d, ks, p_short
                        
                res["legs"] = [
                    {"type": "put", "strike": K_long, "side": "long", "price": p_long},
                    {"type": "put", "strike": best_K_short, "side": "short", "price": p_short_best}
                ]
                res["net_cost"] = (p_long - p_short_best) * 100
                res["margin_req"] = res["net_cost"]
                
            elif direction == "bear_call":
                K_short = S_round + 1
                c_short = bs_call_price(S, K_short, T, r, sigma_adj)
                K_long = K_short + strike_width
                c_long = bs_call_price(S, K_long, T, r, sigma_adj)
                
                res["legs"] = [
                    {"type": "call", "strike": K_short, "side": "short", "price": c_short},
                    {"type": "call", "strike": K_long, "side": "long", "price": c_long}
                ]
                res["net_cost"] = (c_long - c_short) * 100
                res["margin_req"] = (K_long - K_short) * 100
                
            elif direction == "bull_put":
                K_short = S_round - 1
                p_short = bs_put_price(S, K_short, T, r, sigma_adj)
                K_long = K_short - strike_width
                p_long = bs_put_price(S, K_long, T, r, sigma_adj)
                
                res["legs"] = [
                    {"type": "put", "strike": K_short, "side": "short", "price": p_short},
                    {"type": "put", "strike": K_long, "side": "long", "price": p_long}
                ]
                res["net_cost"] = (p_long - p_short) * 100
                res["margin_req"] = (K_short - K_long) * 100

        elif topology == "straddle":
            C_price = bs_call_price(S, S_round, T, r, sigma_adj)
            P_price = bs_put_price(S, S_round, T, r, sigma_adj)
            res["legs"] = [
                {"type": "call", "strike": S_round, "side": "long", "price": C_price},
                {"type": "put", "strike": S_round, "side": "long", "price": P_price}
            ]
            res["net_cost"] = (C_price + P_price) * 100
            res["margin_req"] = res["net_cost"]
            
        elif topology == "iron_condor":
            call_short_k = S_round + int(strike_width / 2)
            call_long_k = call_short_k + strike_width
            
            put_short_k = S_round - int(strike_width / 2)
            put_long_k = put_short_k - strike_width
            
            cs = bs_call_price(S, call_short_k, T, r, sigma_adj)
            cl = bs_call_price(S, call_long_k, T, r, sigma_adj)
            ps = bs_put_price(S, put_short_k, T, r, sigma_adj)
            pl = bs_put_price(S, put_long_k, T, r, sigma_adj)
            
            res["legs"] = [
                {"type": "call", "strike": call_long_k, "side": "long", "price": cl},
                {"type": "call", "strike": call_short_k, "side": "short", "price": cs},
                {"type": "put", "strike": put_short_k, "side": "short", "price": ps},
                {"type": "put", "strike": put_long_k, "side": "long", "price": pl}
            ]
            res["net_cost"] = ((cl - cs) + (pl - ps)) * 100
            res["margin_req"] = strike_width * 100

        elif topology == "butterfly":
             k_lower = S_round - strike_width
             k_mid = S_round
             k_upper = S_round + strike_width
             
             clow = bs_call_price(S, k_lower, T, r, sigma_adj)
             cmid = bs_call_price(S, k_mid, T, r, sigma_adj)
             chigh = bs_call_price(S, k_upper, T, r, sigma_adj)
             
             res["legs"] = [
                {"type": "call", "strike": k_lower, "side": "long", "price": clow},
                {"type": "call", "strike": k_mid, "side": "short", "price": cmid},
                {"type": "call", "strike": k_mid, "side": "short", "price": cmid},
                {"type": "call", "strike": k_upper, "side": "long", "price": chigh}
             ]
             res["net_cost"] = (clow - 2*cmid + chigh) * 100
             res["margin_req"] = res["net_cost"]
             
        return res

    @staticmethod
    def price_topology(legs: List[Dict], S: float, T: float, r: float, sigma: float, realism_factor: float = 1.15) -> float:
        """
        Dynamically calculates the current market value of an ongoing strategy.
        Now includes realism_factor for consistent synthetic pricing.
        """
        val = 0.0
        sigma_adj = sigma * realism_factor
        for leg in legs:
            if leg["type"] == "call":
                p = bs_call_price(S, leg["strike"], T, r, sigma_adj)
            else:
                p = bs_put_price(S, leg["strike"], T, r, sigma_adj)
            
            if leg["side"] == "long":
                val += p * 100
            else:
                val -= p * 100
        return val
