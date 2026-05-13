import math
import numpy as np
from typing import List, Optional

def entropy(probs) -> float:
    """Shannon entropy in bits. Robustly handles scalars and arrays."""
    p = np.asarray(probs).astype(float).flatten()
    
    # If binary (single value provided), construct the full [p, 1-p] distribution
    if p.size == 1:
        p = np.array([p[0], 1.0 - p[0]])
    
    # Clip and re-normalize to ensure it remains a valid probability distribution
    p = np.clip(p, 1e-6, 1.0 - 1e-6)
    p /= np.sum(p)
    
    return -np.sum(p * np.log2(p))

def kl_divergence(p_val, q_val) -> float:
    """
    KL(P || Q) — Information Gain (Edge) in bits.
    Measures how much 'surprise' Grok has compared to the Market.
    """
    p = np.asarray(p_val).astype(float).flatten()
    q = np.asarray(q_val).astype(float).flatten()
    
    # Handle binary scalars: convert p=0.6 to [0.6, 0.4]
    if p.size == 1: p = np.array([p[0], 1.0 - p[0]])
    if q.size == 1: q = np.array([q[0], 1.0 - q[0]])
    
    # Robust clipping and re-normalization
    p = np.clip(p, 1e-6, 1.0 - 1e-6)
    p /= np.sum(p)
    q = np.clip(q, 1e-6, 1.0 - 1e-6)
    q /= np.sum(q)
               
    return np.sum(p * np.log2(p / q))

def max_entropy_fusion(signals: List[float], weights: Optional[List[float]] = None) -> float:
    """
    Combines independent probability signals in log-odds space.
    This is more 'opinionated' than a simple average and better for trading.
    """
    if not signals:
        return 0.5
    
    # Filter out invalid signals
    valid_signals = [np.clip(s, 0.001, 0.999) for s in signals]
    
    if weights is None:
        weights = [1.0] * len(valid_signals)
    
    # Convert to log-odds: L = log(p / (1-p))
    log_odds = [math.log(s / (1 - s)) for s in valid_signals]
    
    # Weighted average of log-odds
    w_sum = sum(weights)
    weighted_log_odds = sum(lo * w for lo, w in zip(log_odds, weights)) / w_sum
    
    # Convert back to probability: p = 1 / (1 + exp(-L))
    return 1 / (1 + math.exp(-weighted_log_odds))

def insider_alert(entropy_history: List[float], threshold_sigma: float = 3.0) -> bool:
    """
    Detects 'Entropy Collapse'—when market uncertainty drops faster than 
    historical volatility suggests, often signaling an insider move or breaking news.
    """
    if len(entropy_history) < 10:
        return False
        
    recent = entropy_history[-1]
    past = np.array(entropy_history[:-1])
    
    mean = np.mean(past)
    std = np.std(past)
    
    if std < 1e-6: # Prevent division by zero
        return False
        
    z_score = (recent - mean) / std
    
    # We specifically look for a DROP in entropy (market becoming too certain)
    return z_score < -threshold_sigma
