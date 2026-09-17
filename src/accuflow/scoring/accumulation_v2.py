"""Frozen, uncalibrated v2 research configuration; independent of the v1 hash."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from statistics import mean

VERSION = "accumulation-v2.0-experimental"
RULES = {
    "version": VERSION, "calibration": "unvalidated_no_13f",
    "weights": {"direction": 30, "absorption": 25, "retention": 20, "persistence": 25},
    "windows": {"5": .25, "10": .50, "20": .25},
    "threshold": 65, "minute_history": 60, "min_minute_history": 20,
    "feature_history": 126, "min_feature_history": 60, "min_day_history": 20,
    "regression_history": 120, "min_regression_history": 60,
    "matched_events": 30, "match_log_volume_distance": .7, "max_matches": 120,
    "response_slots": 6, "volume_cap": 3., "tick_size": .01,
    "percentile_floor": .5, "percentile_width": .45,
    "minimum_coverage": .75, "penalty_weights": {"failed_response": 10, "concentration": 10},
    "support_percentile": .6, "family_gate": .25, "cooldown_sessions": 5,
    "waning_closes": 3, "close_low_closes": 5, "invalidation_closes": 2,
    "scope": "completed_regular_sessions_only",
}
SOURCES = ["scoring/accumulation_v2.py", "features/accumulation_v2.py",
           "detectors/accumulation_v2.py", "state/accumulation_v2.py",
           "features/bars.py", "features/relative.py", "scoring/rules.py",
           "services/quality.py", "services/calendar.py"]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def manifest(threshold=65):
    if threshold not in (50, 55, 60, 65, 70, 75, 80):
        raise ValueError("threshold must be one of 50,55,60,65,70,75,80")
    rules = deepcopy(RULES)
    rules["threshold"] = threshold
    root = Path(__file__).resolve().parents[1]
    code = "".join(name + (root / name).read_text(encoding="utf-8").replace("\r\n", "\n") for name in SOURCES)
    rules["code_hash"] = hashlib.sha256(code.encode()).hexdigest()
    rules["manifest_hash"] = hashlib.sha256(canonical(rules).encode()).hexdigest()
    return rules


def verify_manifest(config):
    if config != manifest(config.get("threshold")):
        raise ValueError("unsupported or modified v2 manifest")


def clip(x, low=0., high=1.):
    return max(low, min(high, x))


def percentile(value, history):
    if value is None or not history:
        return None
    return (sum(x < value for x in history) + .5 * sum(x == value for x in history)) / len(history)


def evidence(rank):
    return 0. if rank is None else clip((rank - .5) / .45)


def mapped(values, histories, minimum):
    if any(v is None or len(h) < minimum for v, h in zip(values, histories)):
        return None
    return mean(evidence(percentile(v, h)) for v, h in zip(values, histories))


def score_interval(families, penalties, config):
    known = sum(w * families[k]["value"] for k, w in config["weights"].items() if families[k]["available"])
    missing = sum(w for k, w in config["weights"].items() if not families[k]["available"])
    deductions = sum(w * penalties[k] for k, w in config["penalty_weights"].items() if penalties[k] is not None)
    unknown_penalty = sum(w for k, w in config["penalty_weights"].items() if penalties[k] is None)
    return round(clip(known - deductions - unknown_penalty, 0, 100), 4), round(clip(known + missing - deductions, 0, 100), 4)
