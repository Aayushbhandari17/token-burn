from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio

from backend.config import TIERS
from backend.mock_llm import mock_llm

app = FastAPI(title="Token Burn Defense Prototype API")

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
    
    # Determine effective max_tokens: use request field, or fallback to tier limit
    effective_max_tokens = request.max_tokens
    if effective_max_tokens is None:
        effective_max_tokens = TIERS[request.tier]["max_tokens_per_req"]
        
    # Phase 3: No defense logic yet. Pass directly to Mock LLM
    # Use latency_per_token_ms=1 for fast testing unless you want full 20ms delays
    llm_result = await mock_llm(
        prompt_text=request.prompt,
        max_tokens=effective_max_tokens,
        latency_per_token_ms=1  # Fast for basic endpoint validation
    )
    
    return llm_result

if __name__ == "__main__":
    async def run_tests():
        print("Running basic /chat API tests...")
        
        # Test 1: Valid request using default max_tokens for the 'free' tier
        req1 = ChatRequest(user_id="user_001", prompt="Hello there, how are you?", tier="free")
        res1 = await chat(req1)
        assert "input_tokens" in res1
        assert "output_tokens" in res1
        assert "cost" in res1
        print("Test 1 (Valid request reaches Mock LLM & returns structure): Passed.")
        
        # Test 2: Invalid tier rejection
        req2 = ChatRequest(user_id="user_002", prompt="Hello", tier="unlimited_platinum")
        try:
            await chat(req2)
            assert False, "Expected HTTPException for invalid tier"
        except HTTPException as e:
            assert e.status_code == 400
            print("Test 2 (Invalid tier correctly rejected): Passed.")
            
        # Test 3: Custom max_tokens overrides tier default
        req3 = ChatRequest(user_id="user_003", prompt="Write an exhaustive 10000 words analysis.", tier="admin", max_tokens=10)
        res3 = await chat(req3)
        assert res3["output_tokens"] == 10, "Did not respect custom max_tokens"
        print("Test 3 (Custom max_tokens overriding tier limit): Passed.")
        
        print("All Phase 3 API tests passed successfully.")
        
    asyncio.run(run_tests())
