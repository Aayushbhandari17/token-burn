import asyncio
import time
from backend.config import PRICE_PER_INPUT_TOKEN, PRICE_PER_OUTPUT_TOKEN, VERBOSE_KEYWORDS

async def mock_llm(prompt_text: str, max_tokens: int, latency_per_token_ms: int = 20) -> dict:
    """
    Simulates an LLM call by estimating tokens, calculating costs, 
    and generating plausible mock output based on the prompt's characteristics.
    """
    # 1. Calculate input tokens
    words = prompt_text.split()
    input_tokens = int(len(words) * 1.3)
    
    # 2. Determine target output tokens based on prompt characteristics
    prompt_lower = prompt_text.lower()
    
    # Base expected output
    target_output = max(50, int(input_tokens * 1.5))
    
    # Multiplier based on verbose keywords
    multiplier = 1.0
    for keyword in VERBOSE_KEYWORDS:
        if keyword in prompt_lower:
            multiplier += 1.0
            
    # Force high output to simulate token burn attacks if certain words are present
    if "10000" in prompt_lower or "exhaustive" in prompt_lower:
        target_output = 4000
    else:
        target_output = int(target_output * multiplier)
        
    # 3. Enforce max_tokens bound
    output_tokens = min(max_tokens, target_output)
    
    # 4. Generate plausible text
    output_words_count = max(1, int(output_tokens / 1.3))
    filler_sentence = "This is a simulated response containing detailed information. "
    filler_words = filler_sentence.split()
    
    generated_words = []
    while len(generated_words) < output_words_count:
        generated_words.extend(filler_words)
    
    text = " ".join(generated_words[:output_words_count])
    
    # 5. Simulate latency
    expected_latency_ms = output_tokens * latency_per_token_ms
    start_time = time.time()
    if expected_latency_ms > 0:
        await asyncio.sleep(expected_latency_ms / 1000.0)
    actual_latency_ms = int((time.time() - start_time) * 1000)
    
    # 6. Calculate costs
    input_cost = input_tokens * PRICE_PER_INPUT_TOKEN
    output_cost = output_tokens * PRICE_PER_OUTPUT_TOKEN
    total_cost = input_cost + output_cost
    
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "text": text,
        "latency_ms": actual_latency_ms,
        "cost": {
            "input_cost": input_cost,
            "output_cost": output_cost,
            "total_cost": total_cost
        }
    }

if __name__ == "__main__":
    async def run_tests():
        print("Running Mock LLM tests...")
        
        # Test 1: Normal prompt
        normal_prompt = "Hello, how are you today?"
        res1 = await mock_llm(normal_prompt, max_tokens=100, latency_per_token_ms=1)
        print(f"Test 1 (Normal): {res1['input_tokens']} in, {res1['output_tokens']} out, cost: ${res1['cost']['total_cost']:.6f}")
        
        assert res1['output_tokens'] <= 100, "Output exceeded max_tokens"
        expected_input = int(len(normal_prompt.split()) * 1.3)
        assert res1['input_tokens'] == expected_input, "Input token calculation incorrect"
        assert res1['cost']['input_cost'] == expected_input * PRICE_PER_INPUT_TOKEN, "Input cost calculation incorrect"
        assert res1['cost']['output_cost'] == res1['output_tokens'] * PRICE_PER_OUTPUT_TOKEN, "Output cost calculation incorrect"
        assert res1['cost']['total_cost'] == res1['cost']['input_cost'] + res1['cost']['output_cost'], "Total cost calculation incorrect"
        assert len(res1['text']) > 0, "No text generated"
        
        # Test 2: Verbose prompt triggering max_tokens
        verbose_prompt = "Write an extremely detailed, comprehensive, and exhaustive analysis. 10000 words."
        res2 = await mock_llm(verbose_prompt, max_tokens=512, latency_per_token_ms=1)
        print(f"Test 2 (Verbose capped): {res2['input_tokens']} in, {res2['output_tokens']} out, cost: ${res2['cost']['total_cost']:.6f}")
        
        assert res2['output_tokens'] == 512, "Output was not capped at max_tokens"
        assert res2['cost']['total_cost'] > res1['cost']['total_cost'], "Verbose prompt did not cost more"
        
        print("All tests passed successfully.")

    asyncio.run(run_tests())
