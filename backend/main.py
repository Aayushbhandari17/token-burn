from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio
import math
from redis.asyncio import Redis

import backend.config as config
from backend.config import TIERS, PRICE_PER_INPUT_TOKEN, PRICE_PER_OUTPUT_TOKEN
from backend.mock_llm import mock_llm
from backend.defense.budget import BudgetEngine, BudgetExceeded

app = FastAPI(title="Token Burn Defense Prototype API")

# Initialize global dependencies
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
    requested_cap = request.max_tokens if request.max_tokens is not None else tier_config["max_tokens_per_req"]
    
    # ---------------------------------------------------------
    # Baseline Flow: Defenses disabled (Unprotected Demo)
    # ---------------------------------------------------------
    if not config.DEFENSES_ENABLED:
        return await mock_llm(
            prompt_text=request.prompt,
            max_tokens=requested_cap,
            latency_per_token_ms=1
        )
    
    # ---------------------------------------------------------
    # Protected Flow: Phase 5 Defense Pipeline
    # ---------------------------------------------------------
    # 1. Input token estimation
    input_tokens = int(len(request.prompt.split()) * 1.3)
    input_cost = input_tokens * PRICE_PER_INPUT_TOKEN
    
    # 2. Dynamic max_tokens & Cost ceiling
    if tier_config["daily_cost_limit"] == math.inf:
        max_tokens = requested_cap
        cost_ceiling = 0.0
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
        
    # 4. Mock LLM Call (No reconciliation yet - reserved ceiling stays fully reserved until Phase 7)
    llm_result = await mock_llm(
        prompt_text=request.prompt,
        max_tokens=max_tokens,
        latency_per_token_ms=1
    )
    
    return llm_result

if __name__ == "__main__":
    async def run_tests():
        print("Running Phase 5 Pre-request Defense Pipeline tests (Modified)...")
        
        try:
            await redis_client.ping()
        except Exception:
            print("Skipping tests: Redis is not running locally.")
            return
            
        test_user = "user_phase5_fix"
        
        async def cleanup():
            await redis_client.delete(f"budget:cost:daily:{test_user}")
            await redis_client.delete(f"budget:req_hour:{test_user}")
            await redis_client.delete(f"budget:req_day:{test_user}")
            await redis_client.delete(f"budget:input:{test_user}")

        # =======================================================
        # TESTS WITH DEFENSES_ENABLED = TRUE
        # =======================================================
        config.DEFENSES_ENABLED = True
        await cleanup()
        
        # Test 1: Normal request successfully reserves budget and reaches Mock LLM
        req1 = ChatRequest(user_id=test_user, prompt="Hello there, how are you?", tier="free")
        res1 = await chat(req1)
        assert "output_tokens" in res1
        
        cost_used = await redis_client.get(f"budget:cost:daily:{test_user}")
        assert cost_used is not None and int(cost_used) > 0, "Cost should have been fully reserved"
        print("Test 1 (Defenses ON - Normal request & budget reservation): Passed.")
        
        # Test 2: Insufficient budget is rejected
        await redis_client.set(f"budget:cost:daily:{test_user}", 2_000_000) # Full $2.00 used
        try:
            req2 = ChatRequest(user_id=test_user, prompt="Hello", tier="free")
            await chat(req2)
            assert False, "Should have been rejected for LIMIT_COST"
        except HTTPException as e:
            assert e.status_code == 429 and "LIMIT_COST" in e.detail
            print("Test 2 (Defenses ON - Insufficient budget rejected): Passed.")
            
        # =======================================================
        # TESTS WITH DEFENSES_ENABLED = FALSE
        # =======================================================
        config.DEFENSES_ENABLED = False
        
        # Test 3: Budget checks bypassed completely when disabled
        # Redis still has max budget (2,000,000) used. The request should succeed anyway.
        req3 = ChatRequest(user_id=test_user, prompt="Hello bypass", tier="free")
        res3 = await chat(req3)
        assert "output_tokens" in res3
        print("Test 3 (Defenses OFF - Bypasses budget checks completely): Passed.")
        
        await cleanup()
        print("All modified Phase 5 tests passed successfully.")
        
    asyncio.run(run_tests())
