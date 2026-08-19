from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio
import math
from redis.asyncio import Redis

from backend.config import TIERS, PRICE_PER_INPUT_TOKEN, PRICE_PER_OUTPUT_TOKEN
from backend.mock_llm import mock_llm
from backend.defense.budget import BudgetEngine, BudgetExceeded

app = FastAPI(title="Token Burn Defense Prototype API")

# Initialize global dependencies (for a real app, use lifespan events or dependency injection)
redis_client = Redis(host='localhost', port=6379, db=0)
budget_engine = BudgetEngine(redis_client)

class ChatRequest(BaseModel):
    user_id: str
    prompt: str
    tier: str
    max_tokens: Optional[int] = None

@app.post("/chat")
async def chat(request: ChatRequest):
    # Validate tier against configuration
    if request.tier not in TIERS:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid tier '{request.tier}'. Valid tiers are: {list(TIERS.keys())}"
        )
        
    tier_config = TIERS[request.tier]
    
    # 1. Input token estimation
    input_tokens = int(len(request.prompt.split()) * 1.3)
    input_cost = input_tokens * PRICE_PER_INPUT_TOKEN
    
    # 2. Dynamic max_tokens & Cost ceiling
    requested_cap = request.max_tokens if request.max_tokens is not None else tier_config["max_tokens_per_req"]
    
    if tier_config["daily_cost_limit"] == math.inf:
        max_tokens = requested_cap
        cost_ceiling = input_cost + (max_tokens * PRICE_PER_OUTPUT_TOKEN)
    else:
        remaining_budget = await budget_engine.get_remaining_daily_cost(request.user_id, request.tier)
        budget_for_output = max(0.0, remaining_budget - input_cost)
        
        # 10% of remaining budget available for output tokens on a single request
        budget_cap_tokens = int((budget_for_output * 0.1) / PRICE_PER_OUTPUT_TOKEN)
        max_tokens = max(1, min(requested_cap, budget_cap_tokens))
        
        cost_ceiling = input_cost + (max_tokens * PRICE_PER_OUTPUT_TOKEN)
        
    # 3. Atomic budget reservation
    try:
        await budget_engine.reserve(request.user_id, request.tier, cost_ceiling, input_tokens)
    except BudgetExceeded as e:
        raise HTTPException(status_code=429, detail=f"Budget or limit exceeded: {e.reason}")
        
    # 4. Mock LLM Call + Reconcile Budget
    try:
        llm_result = await mock_llm(
            prompt_text=request.prompt,
            max_tokens=max_tokens,
            latency_per_token_ms=1
        )
        
        # Step 7: Reconcile Budget (refund unused cost)
        if tier_config["daily_cost_limit"] != math.inf:
            actual_cost = llm_result["cost"]["total_cost"]
            unused_cost = cost_ceiling - actual_cost
            if unused_cost > 0:
                await budget_engine.refund(request.user_id, unused_cost)
                
        return llm_result
        
    except Exception as e:
        # Full refund on LLM exception to prevent burning budget on failed calls
        if tier_config["daily_cost_limit"] != math.inf:
            await budget_engine.refund(request.user_id, cost_ceiling)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    async def run_tests():
        print("Running Phase 5 Pre-request Defense Pipeline tests...")
        
        try:
            await redis_client.ping()
        except Exception:
            print("Skipping tests: Redis is not running locally.")
            return
            
        test_user = "user_phase5"
        
        async def cleanup():
            await redis_client.delete(f"budget:cost:daily:{test_user}")
            await redis_client.delete(f"budget:req_hour:{test_user}")
            await redis_client.delete(f"budget:req_day:{test_user}")
            await redis_client.delete(f"budget:input:{test_user}")

        await cleanup()
        
        # Test 1: Normal request successfully reserves budget and reaches Mock LLM
        req1 = ChatRequest(user_id=test_user, prompt="Hello there, how are you?", tier="free")
        res1 = await chat(req1)
        assert "output_tokens" in res1
        
        cost_used = await redis_client.get(f"budget:cost:daily:{test_user}")
        assert int(cost_used) > 0, "Cost should have been reserved and partially refunded, > 0"
        print("Test 1 (Normal request & budget reservation): Passed.")
        
        # Test 2: Max tokens dynamic logic bounds output based on budget
        # We manually drain the budget to near zero.
        await cleanup()
        # Set used cost to $1.99, leaving $0.01
        await redis_client.set(f"budget:cost:daily:{test_user}", 1_990_000) 
        req2 = ChatRequest(user_id=test_user, prompt="Write an exhaustive 10000 words analysis.", tier="free")
        res2 = await chat(req2)
        # Remaining budget = $0.01. Input is ~10 tokens ($0.000025)
        # Budget for output = $0.01 - $0.000025 ~= $0.009975
        # Cap = 10% = $0.0009975 -> ~99 tokens.
        assert res2["output_tokens"] <= 100, f"Max tokens was not dynamically capped based on budget! Got {res2['output_tokens']}"
        print("Test 4 (max_tokens dynamically capped by remaining budget): Passed.")
        
        # Test 3: Insufficient budget is rejected
        await redis_client.set(f"budget:cost:daily:{test_user}", 2_000_000) # Full $2.00 used
        try:
            req3 = ChatRequest(user_id=test_user, prompt="Hello", tier="free")
            await chat(req3)
            assert False, "Should have been rejected for LIMIT_COST"
        except HTTPException as e:
            assert e.status_code == 429 and "LIMIT_COST" in e.detail
            print("Test 2 (Insufficient budget rejected): Passed.")
            
        # Test 4: Input token limits enforced
        await cleanup()
        await redis_client.set(f"budget:input:{test_user}", 50_000)
        try:
            req4 = ChatRequest(user_id=test_user, prompt="Hello", tier="free")
            await chat(req4)
            assert False, "Should have been rejected for LIMIT_INPUT_TOKENS"
        except HTTPException as e:
            assert e.status_code == 429 and "LIMIT_INPUT_TOKENS" in e.detail
            print("Test 3 (Input/Request limits enforced): Passed.")
            
        # Test 5: Admin tier continues to bypass limits
        req5 = ChatRequest(user_id=test_user, prompt="Hello admin", tier="admin")
        res5 = await chat(req5)
        assert "output_tokens" in res5
        print("Test 5 (Admin bypass intact): Passed.")
        
        await cleanup()
        print("All Phase 5 tests passed successfully.")
        
    asyncio.run(run_tests())
