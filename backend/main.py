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
from backend.logger import RequestLogger

app = FastAPI(title="Token Burn Defense Prototype API")

# Initialize global dependencies
redis_client = Redis(host='localhost', port=6379, db=0)
budget_engine = BudgetEngine(redis_client)
risk_scorer = RiskScorer(redis_client)
request_logger = RequestLogger("backend/data/requests.db")

class ChatRequest(BaseModel):
    user_id: str
    prompt: str
    tier: str
    max_tokens: Optional[int] = None
    conversation_turn: int = 1

@app.post("/chat")
async def chat(request: ChatRequest):
    if request.tier not in TIERS:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid tier '{request.tier}'. Valid tiers are: {list(TIERS.keys())}"
        )
        
    tier_config = TIERS[request.tier]
    requested_cap = request.max_tokens if request.max_tokens is not None else tier_config["max_tokens_per_req"]
    input_tokens = int(len(request.prompt.split()) * 1.3)
    input_cost = input_tokens * PRICE_PER_INPUT_TOKEN
    
    # ---------------------------------------------------------
    # Baseline Flow: Defenses disabled (Unprotected Demo)
    # ---------------------------------------------------------
    if not config.DEFENSES_ENABLED:
        status = "SUCCESS_UNPROTECTED"
        actual_output_tokens = 0
        actual_cost = 0.0
        try:
            llm_result = await mock_llm(request.prompt, requested_cap, latency_per_token_ms=1)
            actual_output_tokens = llm_result["output_tokens"]
            actual_cost = input_cost + (actual_output_tokens * PRICE_PER_OUTPUT_TOKEN)
            return llm_result
        except Exception as e:
            status = "ERROR_UNPROTECTED"
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            request_logger.log_request(
                request.user_id, request.tier, input_tokens, actual_output_tokens,
                requested_cap, actual_cost, 0, "NONE", status
            )
            
    # ---------------------------------------------------------
    # Protected Flow: Phase 6+ Defense Pipeline
    # ---------------------------------------------------------
    is_flagged = await redis_client.get(f"scorer:flagged:{request.user_id}")
    if is_flagged:
        request_logger.log_request(request.user_id, request.tier, input_tokens, 0, requested_cap, 0.0, 10, PolicyAction.HARD_DENY, "BLOCKED_FLAGGED")
        raise HTTPException(status_code=403, detail="Account temporarily flagged for suspicious activity.")

    score, reasons, prompt_hash = await risk_scorer.evaluate(
        user_id=request.user_id, prompt=request.prompt, input_tokens=input_tokens,
        conversation_turn=request.conversation_turn, daily_cost_limit=tier_config["daily_cost_limit"]
    )
    
    action, policy_max_tokens = apply_policy(score, requested_cap)
    
    if action == PolicyAction.HARD_DENY:
        await redis_client.set(f"scorer:flagged:{request.user_id}", "1", ex=3600)
        request_logger.log_request(request.user_id, request.tier, input_tokens, 0, requested_cap, 0.0, score, action, "BLOCKED_HARD_DENY")
        raise HTTPException(status_code=403, detail="Request blocked (Score 10). Account flagged.")
    elif action == PolicyAction.SOFT_DENY:
        request_logger.log_request(request.user_id, request.tier, input_tokens, 0, requested_cap, 0.0, score, action, "BLOCKED_SOFT_DENY")
        raise HTTPException(status_code=429, detail="Request temporarily blocked (Score 8-9). Please try again later.")
        
    requested_cap = policy_max_tokens
    
    if tier_config["daily_cost_limit"] == math.inf:
        max_tokens = requested_cap
        cost_ceiling = 0.0
    else:
        remaining_budget = await budget_engine.get_remaining_daily_cost(request.user_id, request.tier)
        budget_for_output = max(0.0, remaining_budget - input_cost)
        budget_cap_tokens = int((budget_for_output * 0.1) / PRICE_PER_OUTPUT_TOKEN)
        max_tokens = max(1, min(requested_cap, budget_cap_tokens))
        cost_ceiling = input_cost + (max_tokens * PRICE_PER_OUTPUT_TOKEN)
        
    try:
        await budget_engine.reserve(request.user_id, request.tier, cost_ceiling, input_tokens)
    except BudgetExceeded as e:
        request_logger.log_request(request.user_id, request.tier, input_tokens, 0, requested_cap, 0.0, score, action, "REJECTED_BUDGET")
        raise HTTPException(status_code=429, detail=f"Budget or limit exceeded: {e.reason}")
        
    await risk_scorer.update_state(request.user_id, prompt_hash, cost_ceiling)

    cost_to_refund = cost_ceiling
    actual_cost = cost_ceiling
    actual_output_tokens = 0
    status = "ERROR"
    
    try:
        # We dynamically patch mock_llm in tests to test failure refund
        # To avoid circular dependency during patch, we use global or module import, 
        # but here we just call the local imported mock_llm.
        # Tests will monkeypatch `backend.main.mock_llm`.
        llm_result = await globals().get("mock_llm", mock_llm)(
            prompt_text=request.prompt,
            max_tokens=max_tokens,
            latency_per_token_ms=1
        )
        
        actual_output_tokens = llm_result["output_tokens"]
        actual_cost = input_cost + (actual_output_tokens * PRICE_PER_OUTPUT_TOKEN)
        cost_to_refund = max(0.0, cost_ceiling - actual_cost)
        status = "SUCCESS"
        
        llm_result["_debug_score"] = score
        llm_result["_debug_action"] = action
        llm_result["_debug_reasons"] = reasons
        return llm_result
        
    except Exception as e:
        # Full refund happens in finally block automatically since cost_to_refund = cost_ceiling
        raise HTTPException(status_code=500, detail=str(e))
        
    finally:
        # Phase 7: Actual cost reconciliation / refund
        if tier_config["daily_cost_limit"] != math.inf and cost_to_refund > 0:
            await budget_engine.refund(request.user_id, cost_to_refund)
            
        # Phase 7: SQLite Request Logging
        request_logger.log_request(
            user_id=request.user_id,
            tier=request.tier,
            input_tokens=input_tokens,
            output_tokens=actual_output_tokens,
            max_tokens=max_tokens,
            actual_cost=actual_cost if status == "SUCCESS" else 0.0,
            risk_score=score,
            policy_action=action,
            status=status
        )

if __name__ == "__main__":
    import sqlite3
    async def run_tests():
        print("Running Phase 7 Reconciliation & Logging tests...")
        
        try:
            await redis_client.ping()
        except Exception:
            print("Skipping tests: Redis is not running locally.")
            return
            
        test_user = "user_phase7"
        
        async def cleanup():
            keys = await redis_client.keys(f"*{test_user}*")
            if keys:
                await redis_client.delete(*keys)
            with sqlite3.connect(request_logger.db_path) as conn:
                conn.execute(f"DELETE FROM request_log WHERE user_id = '{test_user}'")

        config.DEFENSES_ENABLED = True
        await cleanup()
        
        # Test 1: Successful request refunds unused reserved cost & Test 3: Actual cost calculated
        req1 = ChatRequest(user_id=test_user, prompt="Hello there, how are you?", tier="free", max_tokens=200)
        res1 = await chat(req1)
        
        # Free tier limit = 2,000,000 microdollars. Let's see what was deducted.
        cur_cost_micro = int(await redis_client.get(f"budget:cost:daily:{test_user}"))
        
        expected_input_tokens = int(len("Hello there, how are you?".split()) * 1.3)
        expected_output_tokens = res1["output_tokens"]
        expected_actual_cost = (expected_input_tokens * PRICE_PER_INPUT_TOKEN) + (expected_output_tokens * PRICE_PER_OUTPUT_TOKEN)
        expected_micro = int(expected_actual_cost * 1_000_000)
        
        # Check that cur_cost_micro is very close to expected (allow tiny float rounding diff)
        assert abs(cur_cost_micro - expected_micro) <= 1, f"Expected {expected_micro}, got {cur_cost_micro}"
        print("Test 1 & 3 (Successful refund logic, actual cost calculation): Passed.")
        
        # Test 4: Request is correctly written to SQLite
        with sqlite3.connect(request_logger.db_path) as conn:
            cursor = conn.execute(f"SELECT input_tokens, output_tokens, actual_cost, status FROM request_log WHERE user_id = '{test_user}'")
            rows = cursor.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == expected_input_tokens
            assert rows[0][1] == expected_output_tokens
            assert abs(rows[0][2] - expected_actual_cost) < 0.00001
            assert rows[0][3] == "SUCCESS"
        print("Test 4 (SQLite logging works correctly): Passed.")

        # Test 2: Failed Mock LLM call refunds the full reservation
        await cleanup()
        
        # Temporarily mock the mock_llm to fail
        original_mock = globals().get("mock_llm", mock_llm)
        async def failing_mock(*args, **kwargs):
            raise RuntimeError("Intentional Test Failure")
        globals()["mock_llm"] = failing_mock
        
        req2 = ChatRequest(user_id=test_user, prompt="Fail please", tier="free")
        try:
            await chat(req2)
            assert False, "Should have thrown 500"
        except HTTPException as e:
            assert e.status_code == 500
            
        globals()["mock_llm"] = original_mock  # Restore
        
        # Verify budget was fully refunded (should be 0 or key not exist/cleared since it's the first request)
        cost_after_fail = await redis_client.get(f"budget:cost:daily:{test_user}")
        assert cost_after_fail is None or int(cost_after_fail) == 0, f"Cost should be fully refunded, got {cost_after_fail}"
        print("Test 2 (Failed Mock LLM fully refunds cost): Passed.")
        
        # Test 5: Existing Phase 5-6 defense behavior remains unchanged
        await cleanup()
        req3 = ChatRequest(user_id=test_user, prompt="Please provide an exhaustive and comprehensive analysis.", tier="free", max_tokens=200)
        res3 = await chat(req3)
        assert res3["_debug_score"] == 5
        assert res3["_debug_action"] == PolicyAction.REDUCE_50
        print("Test 5 (Existing defense behaviors intact): Passed.")
        
        await cleanup()
        print("All Phase 7 tests passed successfully.")
        
    asyncio.run(run_tests())
