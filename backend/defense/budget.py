import math
import asyncio
from redis.asyncio import Redis

from backend.config import TIERS

MICRODOLLARS_PER_DOLLAR = 1_000_000

class BudgetExceeded(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Budget exceeded: {reason}")

LUA_RESERVE_SCRIPT = """
local cost_key = KEYS[1]
local req_hour_key = KEYS[2]
local req_day_key = KEYS[3]
local tokens_key = KEYS[4]

local cost_ceil = tonumber(ARGV[1])
local input_tokens = tonumber(ARGV[2])

local cost_limit = tonumber(ARGV[3])
local req_hour_limit = tonumber(ARGV[4])
local req_day_limit = tonumber(ARGV[5])
local tokens_limit = tonumber(ARGV[6])

local cur_cost = tonumber(redis.call('GET', cost_key) or '0')
local cur_req_hour = tonumber(redis.call('GET', req_hour_key) or '0')
local cur_req_day = tonumber(redis.call('GET', req_day_key) or '0')
local cur_tokens = tonumber(redis.call('GET', tokens_key) or '0')

if cost_limit >= 0 and (cur_cost + cost_ceil) > cost_limit then
    return 'LIMIT_COST'
end
if req_hour_limit >= 0 and (cur_req_hour + 1) > req_hour_limit then
    return 'LIMIT_REQ_HOUR'
end
if req_day_limit >= 0 and (cur_req_day + 1) > req_day_limit then
    return 'LIMIT_REQ_DAY'
end
if tokens_limit >= 0 and (cur_tokens + input_tokens) > tokens_limit then
    return 'LIMIT_INPUT_TOKENS'
end

-- All checks passed
redis.call('INCRBY', cost_key, cost_ceil)
redis.call('INCR', req_hour_key)
redis.call('INCR', req_day_key)
redis.call('INCRBY', tokens_key, input_tokens)

if cur_cost == 0 then redis.call('EXPIRE', cost_key, 86400) end
if cur_req_hour == 0 then redis.call('EXPIRE', req_hour_key, 3600) end
if cur_req_day == 0 then redis.call('EXPIRE', req_day_key, 86400) end
if cur_tokens == 0 then redis.call('EXPIRE', tokens_key, 86400) end

return 'OK'
"""

class BudgetEngine:
    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        self._reserve_script = self.redis.register_script(LUA_RESERVE_SCRIPT)

    async def reserve(self, user_id: str, tier: str, cost_ceiling: float, input_tokens: int) -> bool:
        """
        Atomically check and reserve the budget.
        Raises BudgetExceeded if any limit is hit.
        Returns True if successful.
        """
        tier_config = TIERS.get(tier)
        if not tier_config:
            raise ValueError(f"Invalid tier: {tier}")

        # Unlimited bypass for admin
        if tier_config["daily_cost_limit"] == math.inf:
            return True

        # 1. Per-request cost limit check (done in Python to save a Redis call if failing early)
        per_req_limit = tier_config.get("per_request_cost_limit", math.inf)
        if cost_ceiling > per_req_limit:
            raise BudgetExceeded("LIMIT_PER_REQUEST_COST")

        # Convert dollar limits to microdollars
        cost_ceiling_microdollars = int(cost_ceiling * MICRODOLLARS_PER_DOLLAR)
        cost_limit_microdollars = int(tier_config["daily_cost_limit"] * MICRODOLLARS_PER_DOLLAR)

        keys = [
            f"budget:cost:daily:{user_id}",
            f"budget:req_hour:{user_id}",
            f"budget:req_day:{user_id}",
            f"budget:input:{user_id}"
        ]
        args = [
            cost_ceiling_microdollars,
            input_tokens,
            cost_limit_microdollars,
            tier_config["req_per_hour"],
            tier_config["req_per_day"],
            tier_config["input_tokens_per_day"]
        ]

        result = await self._reserve_script(keys=keys, args=args)
        
        # In Python Redis, result may be returned as bytes
        if isinstance(result, bytes):
            result = result.decode('utf-8')

        if result != 'OK':
            raise BudgetExceeded(result)

        return True

    async def refund(self, user_id: str, unused_cost: float):
        """
        Refunds the unused portion of the reserved cost back to the daily limit.
        """
        if unused_cost <= 0:
            return
            
        unused_microdollars = int(unused_cost * MICRODOLLARS_PER_DOLLAR)
        cost_key = f"budget:cost:daily:{user_id}"
        
        # Decrby restores the capacity without breaking atomic limits elsewhere
        await self.redis.decrby(cost_key, unused_microdollars)

if __name__ == "__main__":
    async def run_tests():
        try:
            r = Redis(host='localhost', port=6379, db=0)
            await r.ping()
        except Exception:
            print("Skipping tests: Redis is not running locally. (Please ensure Redis is running to run Phase 4 tests.)")
            return

        print("Redis connected. Running Budget Engine tests...")
        engine = BudgetEngine(r)
        test_user = "test_user_phase4"

        async def cleanup():
            await r.delete(f"budget:cost:daily:{test_user}")
            await r.delete(f"budget:req_hour:{test_user}")
            await r.delete(f"budget:req_day:{test_user}")
            await r.delete(f"budget:input:{test_user}")

        await cleanup()

        # Test 1: Normal reservation succeeds
        await engine.reserve(test_user, "free", cost_ceiling=0.10, input_tokens=500)
        print("Test 1 (Normal reservation): Passed.")

        # Test 6: Check Redis keys use integer microdollars and TTLs
        cost_val = await r.get(f"budget:cost:daily:{test_user}")
        assert cost_val == b"100000", f"Expected 100000 microdollars, got {cost_val}"
        ttl = await r.ttl(f"budget:cost:daily:{test_user}")
        assert ttl > 0, "TTL not set on cost key"
        print("Test 6 (Keys, microdollars, TTLs): Passed.")

        # Test 5: Refund correctly restores unused reserved cost
        await engine.refund(test_user, 0.05)
        cost_val_after_refund = await r.get(f"budget:cost:daily:{test_user}")
        assert cost_val_after_refund == b"50000", f"Expected 50000, got {cost_val_after_refund}"
        print("Test 5 (Refund logic restores cost): Passed.")

        # Test 2: Insufficient cost budget is rejected
        try:
            # Free tier daily limit is $2.00. We have used $0.05. Reserving $1.96 should fail.
            await engine.reserve(test_user, "free", cost_ceiling=1.96, input_tokens=10)
            assert False, "Should have been rejected due to cost limit"
        except BudgetExceeded as e:
            assert "LIMIT_COST" in str(e)
            print("Test 2 (Insufficient cost budget): Passed.")

        # Test 3: Request/token limits enforced
        # Free tier input limit is 50,000. Let's try to reserve 51,000.
        try:
            await engine.reserve(test_user, "free", cost_ceiling=0.01, input_tokens=51000)
            assert False, "Should have been rejected due to input limit"
        except BudgetExceeded as e:
            assert "LIMIT_INPUT_TOKENS" in str(e)
            print("Test 3 (Input token limit enforced): Passed.")

        # Test 4: Concurrent reservations safely bounded
        await cleanup()

        # Send 10 concurrent requests of $0.25 each. 
        # Total limit = $2.00, so exactly 8 should pass and 2 should fail.
        async def spam_reserve():
            try:
                await engine.reserve(test_user, "free", cost_ceiling=0.25, input_tokens=100)
                return True
            except BudgetExceeded:
                return False

        results = await asyncio.gather(*(spam_reserve() for _ in range(10)))
        successes = sum(results)
        failures = len(results) - successes
        assert successes == 8, f"Expected 8 successful reservations, got {successes}"
        assert failures == 2, f"Expected 2 failures, got {failures}"
        
        final_cost = await r.get(f"budget:cost:daily:{test_user}")
        assert final_cost == b"2000000"
        print("Test 4 (Concurrent reservations safely bounded): Passed.")
        
        await cleanup()
        print("All Phase 4 tests passed successfully.")

    asyncio.run(run_tests())
