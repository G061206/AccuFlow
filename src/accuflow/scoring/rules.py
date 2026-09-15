"""Frozen experimental rules, shared by every evidence path."""
import hashlib
import json
from pathlib import Path

RULE_VERSION = "unified-v1-m2.1"
RULES = {
    "version": RULE_VERSION,
    "weights": {"core_behavior":30, "cross_day":25, "price_response":20,
                "relative_strength":15, "distribution":10},
    "cross_day_weights": [1.0,0.85,0.72,0.61,0.52],
    "minute_coverage":0.95, "max_gap_minutes":3, "min_baseline_days":20,
    "target_baseline_days":60, "min_daily_days":60,
    "flow_connection_coverage":0.95, "classified_coverage":0.70,
    "min_flow_trades_per_slot":5, "min_flow_slots_per_hour":8,
    "min_positive_slots":7, "quote_age_seconds":1.0,
    "quote_age_sensitivity":[0.5,1.0,2.0], "matched_sell_samples":30,
    "support_atr_width":0.1, "support_exit_atr":0.25,
    "invalidation_atr":0.25, "response_minutes":15,
    "percentile_floor":0.50, "percentile_width":0.40,
    "observation_score":55, "new_score":65, "strong_score":78,
    "enhancement_delta":10, "cooldown_hours":2, "max_enhancements_per_day":2,
    "contradiction_cap":30, "penalties":{"opposite_response":15,"largest_slot":10,"edges_only":10},
    "relative_ridge":0.0001, "relative_huber_iterations":10,
}
# The checksum covers both configuration and the pure implementation. A code change
# cannot silently replay old evidence under unchanged numeric settings.
_root=Path(__file__).resolve().parents[1]
_sources=["scoring/rules.py","domain/signals.py","detectors/unified.py","state/episodes.py",
          "features/bars.py","features/orderflow.py","features/relative.py","features/extract.py",
          "services/quality.py","services/calendar.py"]
RULE_HASH = hashlib.sha256((json.dumps(RULES,sort_keys=True,separators=(",", ":"))+
    "".join(name+(_root/name).read_text(encoding="utf-8").replace("\r\n","\n") for name in _sources)).encode()).hexdigest()


def percentile(value, history):
    if not history: return None
    return (sum(x < value for x in history) + 0.5 * sum(x == value for x in history)) / len(history)


def evidence(rank):
    if rank is None: return 0.0
    return max(0.0,min(1.0,(rank-RULES["percentile_floor"])/RULES["percentile_width"]))


def unified_score(families, penalties):
    # Missing families contribute zero. Never rescale the remaining weights.
    total = sum(weight * max(0.0,min(1.0,families.get(name,{}).get("value",0.0)))
                if families.get(name,{}).get("available",False) else 0.0
                for name,weight in RULES["weights"].items())
    deduction = min(RULES["contradiction_cap"],sum(RULES["penalties"][name] for name in set(penalties)))
    return round(max(0.0,min(100.0,total-deduction)),2), deduction
