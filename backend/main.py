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
from backend.defense.scorer import RiskScorer
from backend.defense.policy import apply_policy, PolicyAction

app = FastAPI(title="Token Burn Defense Prototype API")

# Initialize global dependencies
redis_client = Redis(host='localhost', port=6379, db=0)
budget_engine = BudgetEngine(redis_client)
risk_scorer = RiskScorer(redis_client)

class ChatRequest(BaseModel):
    user_id: str
    prompt: str
    tier: str
    max_tokens: Optional[int] = None
    conversation_turn: int = 1

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
    # Protected Flow: Phase 6 Defense Pipeline
    # ---------------------------------------------------------
    
    # Phase 6: Check for Hard Deny User Flag (1 hour timeout)
    is_flagged = await redis_client.get(f"scorer:flagged:{request.user_id}")
    if is_flagged:
        raise HTTPException(status_code=403, detail="Account temporarily flagged for suspicious activity.")

    # 1. Input token estimation
    input_tokens = int(len(request.prompt.split()) * 1.3)
    input_cost = input_tokens * PRICE_PER_INPUT_TOKEN
    
    # 2. Phase 6: Scorer & Policy
    score, reasons, prompt_hash = await risk_scorer.evaluate(
        user_id=request.user_id,
        prompt=request.prompt,
        input_tokens=input_tokens,
        conversation_turn=request.conversation_turn,
        daily_cost_limit=tier_config["daily_cost_limit"]
    )
    
    action, policy_max_tokens = apply_policy(score, requested_cap)
    
    # Enforcement Actions
    if action == PolicyAction.HARD_DENY:
        # Score 10 -> Hard deny + 1 hr flag
        await redis_client.set(f"scorer:flagged:{request.user_id}", "1", ex=3600)
        raise HTTPException(status_code=403, detail="Request blocked (Score 10). Account flagged.")
    elif action == PolicyAction.SOFT_DENY:
        # Score 8-9 -> Soft deny (retry after)
        raise HTTPException(status_code=429, detail="Request temporarily blocked (Score 8-9). Please try again later.")
        
    # Action ALLOW, REDUCE_50, or REDUCE_MIN continues but replaces the requested cap
    requested_cap = policy_max_tokens
    
    # 3. Dynamic max_tokens & Cost ceiling
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
        
    # 4. Atomic budget reservation
    try:
        await budget_engine.reserve(request.user_id, request.tier, cost_ceiling, input_tokens)
    except BudgetExceeded as e:
        raise HTTPException(status_code=429, detail=f"Budget or limit exceeded: {e.reason}")
        
    # 5. Phase 6: Update Scorer State (Burst, Velocity, Repetition)
    await risk_scorer.update_state(request.user_id, prompt_hash, cost_ceiling)

    # 6. Mock LLM Call (No reconciliation yet)
    llm_result = await mock_llm(
        prompt_text=request.prompt,
        max_tokens=max_tokens,
        latency_per_token_ms=1
    )
    
    # Append score info to response purely for testing/debugging observability
    llm_result["_debug_score"] = score
    llm_result["_debug_action"] = action
    llm_result["_debug_reasons"] = reasons
    
    return llm_result

if __name__ == "__main__":
    async def run_tests():
        print("Running Phase 6 Scorer & Policy Engine tests...")
        
        try:
            await redis_client.ping()
        except Exception:
            print("Skipping tests: Redis is not running locally.")
            return
            
        test_user = "user_phase6"
        
        async def cleanup():
            keys = await redis_client.keys(f"*{test_user}*")
            if keys:
                await redis_client.delete(*keys)

        # =======================================================
        # TESTS WITH DEFENSES_ENABLED = TRUE
        # =======================================================
        config.DEFENSES_ENABLED = True
        await cleanup()
        
        # Test 1: Normal request (Score 0) -> ALLOW
        req1 = ChatRequest(user_id=test_user, prompt="Hello there, how are you?", tier="free", max_tokens=200)
        res1 = await chat(req1)
        assert res1["_debug_score"] == 0
        assert res1["_debug_action"] == PolicyAction.ALLOW
        print("Test 1 (Normal request Score 0 -> ALLOW): Passed.")
        
        # Test 2: Verbose Prompt (Score 5: 2+ keywords) -> REDUCE_50
        req2 = ChatRequest(user_id=test_user, prompt="Please provide an exhaustive and comprehensive analysis.", tier="free", max_tokens=200)
        res2 = await chat(req2)
        assert res2["_debug_score"] == 5
        assert res2["_debug_action"] == PolicyAction.REDUCE_50
        assert res2["output_tokens"] <= 100, f"Expected tokens capped by 50% policy (<=100), got {res2['output_tokens']}"
        print("Test 2 (Verbose prompt Score 5 -> REDUCE_50): Passed.")
        
        # Test 3: Repeated prompt hash (Score 5) -> REDUCE_50
        # First send a normal prompt
        req_setup = ChatRequest(user_id=test_user, prompt="What is the weather like today?", tier="free", max_tokens=200)
        await chat(req_setup)
        # Then repeat it
        req3 = ChatRequest(user_id=test_user, prompt="What is the weather like today?", tier="free", max_tokens=200)
        res3 = await chat(req3)
        assert "Repeated prompt hash" in res3["_debug_reasons"]
        assert res3["_debug_score"] == 5
        print("Test 3 (Repeated prompt hash detected, Score 5): Passed.")
        
        # Test 4: Explicit length + Conversational Turn + Verbose (Score 4 + 4 + 5 = 10) -> HARD_DENY
        try:
            req4 = ChatRequest(user_id=test_user, prompt="Write 10000 words. Be comprehensive and exhaustive.", tier="free", conversation_turn=35)
            await chat(req4)
            assert False, "Should have been hard denied"
        except HTTPException as e:
            assert e.status_code == 403 and "Score 10" in e.detail
            print("Test 4 (High score -> HARD_DENY flag applied): Passed.")
            
        # Test 5: Verify User is Flagged
        try:
            req5 = ChatRequest(user_id=test_user, prompt="Simple prompt", tier="free")
            await chat(req5)
            assert False, "Should have been denied because user is flagged"
        except HTTPException as e:
            assert e.status_code == 403 and "temporarily flagged" in e.detail
            print("Test 5 (Flagged user cannot make further requests): Passed.")

        # =======================================================
        # TESTS WITH DEFENSES_ENABLED = FALSE
        # =======================================================
        config.DEFENSES_ENABLED = False
        
        # Despite being flagged and having a high score prompt, it should bypass everything
        req6 = ChatRequest(user_id=test_user, prompt="Write 10000 words. Be comprehensive and exhaustive.", tier="free", conversation_turn=35)
        res6 = await chat(req6)
        assert "output_tokens" in res6
        print("Test 6 (DEFENSES_ENABLED=False bypasses policy and flag completely): Passed.")
        
        await cleanup()
        print("All Phase 6 tests passed successfully.")
        
    asyncio.run(run_tests())
