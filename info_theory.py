"""Shannon/Thorp Information Theory Tools"""
import math
import numpy as np
from typing import List, Optional

try:
    from scipy.optimize import minimize
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    minimize = None
    print("⚠️  scipy not installed — using simple average for fusion")


def entropy(probs) -> float:
    """Shannon entropy in bits. Handles list, tuple, or numpy array/scalar."""
    probs = np.asarray(probs).flatten()
    probs = np.clip(probs, 0.01, 0.99)
    return -np.sum([p * math.log2(p) for p in probs])


def kl_divergence(p: List[float], q: List[float]) -> float:
    """KL(P || Q) — edge in bits."""
    p = np.clip(np.asarray(p).flatten(), 0.01, 0.99)
    q = np.clip(np.asarray(q).flatten(), 0.01, 0.99)
    return sum(pi * math.log2(pi / qi) for pi, qi in zip(p, q))


def max_entropy_fusion(signals: List[float], weights: Optional[List[float]] = None) -> float:
    """Jaynes max-entropy fusion with scipy fallback."""
    if not signals:
        return 0.5

    if weights is None:
        weights = [1.0] * len(signals)
    weights = np.array(weights) / sum(weights)

    if not SCIPY_AVAILABLE or len(signals) == 1:
        return float(np.dot(weights, signals))

    def neg_entropy(x):
        # x is numpy array from optimizer → flatten
        return -entropy(x)

    cons = {'type': 'eq', 'fun': lambda x: np.dot(weights, (x - signals))}
    res = minimize(neg_entropy, x0=0.5, bounds=[(0.01, 0.99)], constraints=cons, tol=1e-8)
    return float(res.x[0])


def insider_alert(entropy_history: List[float], threshold_sigma: float = 3.0) -> bool:
    """Detect entropy collapse (insider move)."""
    if len(entropy_history) < 10:
        return False
    recent = entropy_history[-1]
    mean = np.mean(entropy_history[:-1])
    std = np.std(entropy_history[:-1]) or 0.001
    return abs(recent - mean) > threshold_sigma * std
