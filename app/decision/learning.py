"""Tiny CPU quality predictor. Shadow-only; it cannot authorize an action."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

FEATURE_VERSION = 'precall-numeric-v1'
FEATURES = ['bias', 'utf8_bytes_4096', 'digits_100', 'lines_20', 'json_terms_10']


def features(prompt: str) -> list[float]:
    return [1.0, min(len(prompt.encode()) / 4096, 1),
            min(sum(c.isdigit() for c in prompt) / 100, 1),
            min(len(prompt.splitlines()) / 20, 1),
            min(sum(prompt.lower().count(s) for s in ('json', 'null', 'true', 'false')) / 10, 1)]


def predict(weights: list[float], x: list[float]) -> float:
    z = max(-30, min(30, sum(a*b for a, b in zip(weights, x))))
    return 1 / (1 + math.exp(-z))


def fit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [r for r in rows if r['split'] == 'train' and len(r['candidates']) == 2
              and all(c['error'] is None for c in r['candidates'])]
    models = {}
    for channel in ('local', 'step'):
        y = [int(next(c for c in r['candidates'] if c['candidate'] == channel)['checks']['passed']) for r in usable]
        if len(set(y)) < 2:
            models[channel] = {'status': 'single_class_unidentifiable', 'n': len(y), 'positives': sum(y)}
            continue
        x = [features(r['prompt']) for r in usable]
        w = [0.0] * len(FEATURES)
        for _ in range(500):
            gradient = [0.0] * len(w)
            for xi, yi in zip(x, y):
                error = predict(w, xi) - yi
                for j in range(len(w)):
                    gradient[j] += error * xi[j] / len(y)
            w = [v - 0.2*(g + (0.05*v if j else 0)) for j, (v, g) in enumerate(zip(w, gradient))]
        models[channel] = {'status': 'fitted', 'weights': w, 'n': len(y), 'positives': sum(y)}
    return {'schema': 'quality-logistic-v1', 'feature_version': FEATURE_VERSION, 'features': FEATURES,
            'seed': 0, 'iterations': 500, 'learning_rate': 0.2, 'l2': 0.05,
            'mode': 'shadow_only', 'scores_are_calibrated': False, 'models': models,
            'training_sha256': hashlib.sha256(json.dumps(usable, sort_keys=True).encode()).hexdigest()}


def shadow(prompt: str, model: dict[str, Any], legal: list[str]) -> dict[str, Any]:
    scores = {k: predict(v['weights'], features(prompt)) for k, v in model['models'].items()
              if v['status'] == 'fitted' and k in legal}
    return {'mode': 'shadow_only', 'feature_version': FEATURE_VERSION, 'raw_quality_scores': scores,
            'score_kind': 'uncalibrated_quality_estimate', 'executed_action_changed': False}
