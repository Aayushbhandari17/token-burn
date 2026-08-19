from typing import Tuple
from backend.config import POLICY_TIER_MINIMUM_TOKENS

class PolicyAction:
    ALLOW = "ALLOW"
    REDUCE_50 = "REDUCE_50"
    REDUCE_MIN = "REDUCE_MIN"
    SOFT_DENY = "SOFT_DENY"
    HARD_DENY = "HARD_DENY"

def apply_policy(score: int, requested_max_tokens: int) -> Tuple[str, int]:
    """
    Applies the defense policy based on the calculated risk score.
    Returns: (PolicyAction string, modified_max_tokens)
    """
    if score <= 4:
        # 0-4 -> Allow
        return PolicyAction.ALLOW, requested_max_tokens
        
    elif score <= 6:
        # 5-6 -> Reduce max_tokens by 50%
        reduced_tokens = max(1, int(requested_max_tokens * 0.5))
        return PolicyAction.REDUCE_50, reduced_tokens
        
    elif score == 7:
        # 7 -> Reduce max_tokens to tier minimum (128)
        return PolicyAction.REDUCE_MIN, min(requested_max_tokens, POLICY_TIER_MINIMUM_TOKENS)
        
    elif score <= 9:
        # 8-9 -> Soft deny
        return PolicyAction.SOFT_DENY, requested_max_tokens
        
    else:
        # 10 -> Hard deny + one-hour user flag
        return PolicyAction.HARD_DENY, requested_max_tokens
