import hashlib
import re
from typing import Tuple, List
from redis.asyncio import Redis

from backend.config import SCORER_RULES, VERBOSE_KEYWORDS, BURST_LIMIT_REQUESTS, VELOCITY_LIMIT_PERCENTAGE

class RiskScorer:
    def __init__(self, redis_client: Redis):
        self.redis = redis_client

    async def evaluate(self, user_id: str, prompt: str, input_tokens: int, conversation_turn: int, daily_cost_limit: float) -> Tuple[int, List[str], str]:
        """
        Evaluates a request against the rule-based heuristic anomaly scorer.
        Returns: (score, list of triggered rule reasons, prompt_hash)
        """
        score = 0
        reasons = []

        # 1. Input token count thresholds
        if input_tokens > 2000:
            score += SCORER_RULES["input_gt_2000"]
            reasons.append(f"Input tokens > 2000 ({input_tokens})")
        if input_tokens > 5000:
            score += SCORER_RULES["input_gt_5000"]  # Cumulative
            reasons.append(f"Input tokens > 5000 ({input_tokens})")

        # 2. Verbose keywords
        prompt_lower = prompt.lower()
        keyword_matches = sum(1 for kw in VERBOSE_KEYWORDS if kw in prompt_lower)
        if keyword_matches >= 2:
            score += SCORER_RULES["verbose_keywords_2_plus"]
            reasons.append(f"Contains 2+ verbose keywords ({keyword_matches} matches)")

        # 3. Explicit length requests (e.g. "10000 words")
        if re.search(r'\b\d+\s+(words|pages)\b', prompt_lower) or re.search(r'\b(write|generate)\s+\d+\b', prompt_lower):
            score += SCORER_RULES["explicit_length_request"]
            reasons.append("Explicit length request detected")

        # 4. Conversation turns
        if conversation_turn > 15:
            score += SCORER_RULES["turn_gt_15"]
            reasons.append(f"Conversation turn > 15 ({conversation_turn})")
        if conversation_turn > 30:
            score += SCORER_RULES["turn_gt_30"]
            reasons.append(f"Conversation turn > 30 ({conversation_turn})")

        # 5. Request Burst (Requests in last 10 minutes)
        burst_key = f"scorer:burst:{user_id}"
        burst_count = int(await self.redis.get(burst_key) or 0)
        if burst_count >= BURST_LIMIT_REQUESTS:
            score += SCORER_RULES["burst_limit_exceeded"]
            reasons.append(f"Burst limit exceeded ({burst_count} requests in 10m)")

        # 6. Cost Velocity (Cost in last 1 hour > 70% of daily limit)
        if daily_cost_limit != float('inf') and daily_cost_limit > 0:
            velocity_key = f"scorer:cost_1h:{user_id}"
            recent_cost = float(await self.redis.get(velocity_key) or 0.0)
            if recent_cost > (VELOCITY_LIMIT_PERCENTAGE * daily_cost_limit):
                score += SCORER_RULES["velocity_limit_exceeded"]
                reasons.append(f"Velocity exceeded > {int(VELOCITY_LIMIT_PERCENTAGE*100)}% daily limit")

        # 7. Repeated prompt hash
        prompt_hash = hashlib.md5(prompt.encode('utf-8')).hexdigest()
        last_hash = await self.redis.get(f"scorer:last_hash:{user_id}")
        if last_hash and last_hash.decode('utf-8') == prompt_hash:
            score += SCORER_RULES["prompt_repetition"]
            reasons.append("Repeated prompt hash")

        # Cap score at 10
        score = min(10, score)
        return score, reasons, prompt_hash

    async def update_state(self, user_id: str, prompt_hash: str, cost_ceiling: float):
        """
        Updates the signals for burst, velocity, and repetition checks.
        """
        # Burst: 10 minute rolling window (approximated with TTL)
        burst_key = f"scorer:burst:{user_id}"
        if await self.redis.incr(burst_key) == 1:
            await self.redis.expire(burst_key, 600)  # 10 minutes

        # Velocity: 1 hour rolling window (approximated with TTL)
        velocity_key = f"scorer:cost_1h:{user_id}"
        if await self.redis.incrbyfloat(velocity_key, cost_ceiling) == cost_ceiling:
            await self.redis.expire(velocity_key, 3600)  # 1 hour

        # Repetition: Remember last prompt hash for 1 hour
        await self.redis.set(f"scorer:last_hash:{user_id}", prompt_hash, ex=3600)
