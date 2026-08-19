from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio
import math
import json
import time
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

async def publish_event(event_data: dict):
    await redis_client.publish("events", json.dumps(event_data))

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
            await publish_event({
                "timestamp": time.time(),
                "user_id": request.user_id,
                "tier": request.tier,
                "input_tokens": input_tokens,
                "output_tokens": actual_output_tokens,
                "max_tokens": requested_cap,
                "actual_cost": actual_cost,
                "risk_score": 0,
                "policy_action": "NONE",
                "status": status
            })
            
    # ---------------------------------------------------------
    # Protected Flow: Phase 6+ Defense Pipeline
    # ---------------------------------------------------------
    is_flagged = await redis_client.get(f"scorer:flagged:{request.user_id}")
    if is_flagged and tier_config["daily_cost_limit"] != math.inf:
        request_logger.log_request(request.user_id, request.tier, input_tokens, 0, requested_cap, 0.0, 10, PolicyAction.HARD_DENY, "BLOCKED_FLAGGED")
        await publish_event({
            "timestamp": time.time(), "user_id": request.user_id, "tier": request.tier,
            "input_tokens": input_tokens, "output_tokens": 0, "max_tokens": requested_cap,
            "actual_cost": 0.0, "risk_score": 10, "policy_action": PolicyAction.HARD_DENY, "status": "BLOCKED_FLAGGED"
        })
        raise HTTPException(status_code=403, detail="Account temporarily flagged for suspicious activity.")

    if tier_config["daily_cost_limit"] != math.inf:
        score, reasons, prompt_hash = await risk_scorer.evaluate(
            user_id=request.user_id, prompt=request.prompt, input_tokens=input_tokens,
            conversation_turn=request.conversation_turn, daily_cost_limit=tier_config["daily_cost_limit"]
        )
        
        action, policy_max_tokens = apply_policy(score, requested_cap)
        
        if action == PolicyAction.HARD_DENY:
            await redis_client.set(f"scorer:flagged:{request.user_id}", "1", ex=3600)
            request_logger.log_request(request.user_id, request.tier, input_tokens, 0, requested_cap, 0.0, score, action, "BLOCKED_HARD_DENY")
            await publish_event({
                "timestamp": time.time(), "user_id": request.user_id, "tier": request.tier,
                "input_tokens": input_tokens, "output_tokens": 0, "max_tokens": requested_cap,
                "actual_cost": 0.0, "risk_score": score, "policy_action": action, "status": "BLOCKED_HARD_DENY"
            })
            raise HTTPException(status_code=403, detail="Request blocked (Score 10). Account flagged.")
        elif action == PolicyAction.SOFT_DENY:
            request_logger.log_request(request.user_id, request.tier, input_tokens, 0, requested_cap, 0.0, score, action, "BLOCKED_SOFT_DENY")
            await publish_event({
                "timestamp": time.time(), "user_id": request.user_id, "tier": request.tier,
                "input_tokens": input_tokens, "output_tokens": 0, "max_tokens": requested_cap,
                "actual_cost": 0.0, "risk_score": score, "policy_action": action, "status": "BLOCKED_SOFT_DENY"
            })
            raise HTTPException(status_code=429, detail="Request temporarily blocked (Score 8-9). Please try again later.")
            
        requested_cap = policy_max_tokens
    else:
        score, action, reasons, prompt_hash = 0, PolicyAction.ALLOW, [], ""

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
        await publish_event({
            "timestamp": time.time(), "user_id": request.user_id, "tier": request.tier,
            "input_tokens": input_tokens, "output_tokens": 0, "max_tokens": requested_cap,
            "actual_cost": 0.0, "risk_score": score, "policy_action": action, "status": "REJECTED_BUDGET"
        })
        raise HTTPException(status_code=429, detail=f"Budget or limit exceeded: {e.reason}")
        
    await risk_scorer.update_state(request.user_id, prompt_hash, cost_ceiling)

    cost_to_refund = cost_ceiling
    actual_cost = cost_ceiling
    actual_output_tokens = 0
    status = "ERROR"
    
    try:
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
        raise HTTPException(status_code=500, detail=str(e))
        
    finally:
        if tier_config["daily_cost_limit"] != math.inf and cost_to_refund > 0:
            await budget_engine.refund(request.user_id, cost_to_refund)
            
        final_actual_cost = actual_cost if status == "SUCCESS" else 0.0
            
        request_logger.log_request(
            user_id=request.user_id, tier=request.tier, input_tokens=input_tokens,
            output_tokens=actual_output_tokens, max_tokens=max_tokens,
            actual_cost=final_actual_cost, risk_score=score,
            policy_action=action, status=status
        )
        
        await publish_event({
            "timestamp": time.time(), "user_id": request.user_id, "tier": request.tier,
            "input_tokens": input_tokens, "output_tokens": actual_output_tokens, "max_tokens": max_tokens,
            "actual_cost": final_actual_cost, "risk_score": score, "policy_action": action, "status": status
        })

if __name__ == "__main__":
    import sqlite3
    async def run_tests():
        print("Running Phase 7 (Fix) Pub/Sub Event tests...")
        
        try:
            await redis_client.ping()
        except Exception:
            print("Skipping tests: Redis is not running locally.")
            return
            
        test_user = "user_phase7_fix"
        
        async def cleanup():
            keys = await redis_client.keys(f"*{test_user}*")
            if keys:
                await redis_client.delete(*keys)

        config.DEFENSES_ENABLED = True
        await cleanup()
        
        # Test 6: Pub/Sub Structured Event Published
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("events")
        
        req = ChatRequest(user_id=test_user, prompt="Pubsub test", tier="free", max_tokens=50)
        await chat(req)
        
        msg = None
        # Try fetching the message (might take a short cycle to arrive)
        for _ in range(10):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg:
                break
                
        assert msg is not None, "No pubsub message received"
        event_payload = json.loads(msg["data"])
        
        assert event_payload["user_id"] == test_user
        assert event_payload["status"] == "SUCCESS"
        assert event_payload["tier"] == "free"
        assert "actual_cost" in event_payload
        assert "timestamp" in event_payload
        print("Test 6 (Redis Pub/Sub Event Publishing): Passed.")
        
        await pubsub.unsubscribe("events")
        await cleanup()
        print("All Phase 7 (Fix) tests passed successfully.")
        
    asyncio.run(run_tests())
