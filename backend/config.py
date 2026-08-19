import math

# ==============================================================================
# Token Burn Prototype — Configuration
# ==============================================================================

# Toggle all defenses (useful for live demo before/after comparisons)
# Requires a server restart if changed during runtime.
DEFENSES_ENABLED = True

# ==============================================================================
# Pricing Constants
# ==============================================================================
# Based on the plan (GPT-4o-mini scale for realistic math):
PRICE_PER_INPUT_TOKEN = 0.0000025   # $0.0000025 / token
PRICE_PER_OUTPUT_TOKEN = 0.00001    # $0.00001 / token

# ==============================================================================
# Budget Tiers
# ==============================================================================
TIERS = {
    "free": {
        "daily_cost_limit": 2.00,        # $2.00
        "req_per_hour": 10,
        "req_per_day": 50,
        "max_tokens_per_req": 512,
        "input_tokens_per_day": 50_000,
    },
    "pro": {
        "daily_cost_limit": 20.00,       # $20.00
        "req_per_hour": 60,
        "req_per_day": 500,
        "max_tokens_per_req": 2048,
        "input_tokens_per_day": 500_000,
    },
    "admin": {
        "daily_cost_limit": math.inf,
        "req_per_hour": math.inf,
        "req_per_day": math.inf,
        "max_tokens_per_req": 4096,
        "input_tokens_per_day": math.inf,
    }
}

# ==============================================================================
# Velocity & Burst Limits
# ==============================================================================
BURST_LIMIT_REQUESTS = 8           # Requests in last 10 minutes
VELOCITY_LIMIT_PERCENTAGE = 0.70   # Cost in last 1 hour > 70% of daily limit

# ==============================================================================
# Heuristic Scorer Config
# ==============================================================================
# Keywords to trigger complexity/verbose matching
VERBOSE_KEYWORDS = [
    "exhaustive", "comprehensive", "detailed", "thorough",
    "every possible", "all possible", "10000"
]

SCORER_RULES = {
    "input_gt_2000": 3,
    "input_gt_5000": 5,                 # Cumulative if > 5000
    "verbose_keywords_2_plus": 5,       # 2+ keywords from VERBOSE_KEYWORDS
    "explicit_length_request": 4,       # e.g., "write N words", "N pages"
    "turn_gt_15": 2,
    "turn_gt_30": 4,
    "burst_limit_exceeded": 3,
    "velocity_limit_exceeded": 3,
    "prompt_repetition": 5
}

# ==============================================================================
# Policy Action Thresholds
# ==============================================================================
POLICY_THRESHOLDS = {
    "reduce_50_percent": 5,       # Score 5-6 -> Reduce max_tokens by 50%
    "reduce_to_minimum": 7,       # Score 7 -> Reduce max_tokens to 128
    "soft_deny": 8,               # Score 8-9 -> Soft deny (retry-after message)
    "hard_deny": 10               # Score 10 -> Hard deny (1 hour Redis TTL ban)
}

POLICY_TIER_MINIMUM_TOKENS = 128
