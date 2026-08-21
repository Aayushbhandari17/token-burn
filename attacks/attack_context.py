import asyncio
import aiohttp
import time

URL = "http://127.0.0.1:8000/chat"
FILLER = "filler " * 1000  # ~1000 words

async def send_request(session, user_id, tier, prompt):
    payload = {
        "user_id": user_id,
        "tier": tier,
        "prompt": prompt
    }
    async with session.post(URL, json=payload) as response:
        return response.status, await response.json()

async def run_context_attack(tier="free", user_id=None):
    if not user_id:
        user_id = f"context_{tier}_{int(time.time())}"
    print(f"\n--- Running Context Stuffer Attack on '{tier}' tier ---")
    
    current_prompt = "Start of prompt. "
    allowed = 0
    denied = 0
    
    async with aiohttp.ClientSession() as session:
        for i in range(1, 12):  # Send up to 11 requests
            current_prompt += FILLER
            
            print(f"Sending Request {i} with approx {len(current_prompt.split())} words...")
            status, data = await send_request(session, user_id, tier, current_prompt)
            
            input_tokens = data.get("input_tokens") if status == 200 else "N/A"
            if status == 200:
                allowed += 1
                defense_action = data.get("_debug_action", "NONE")
                print(f"  [ALLOW] HTTP 200 | Input tokens: {input_tokens} | Action: {defense_action}")
            else:
                denied += 1
                print(f"  [DENY] HTTP {status} | Error: {data.get('detail')}")

    print(f"\nTier: {tier}")
    print(f"Total Attempted: 11")
    print(f"Allowed: {allowed}")
    print(f"Denied: {denied}")

if __name__ == "__main__":
    async def main():
        timestamp = int(time.time())
        await run_context_attack("free", f"context_free_{timestamp}")
        await run_context_attack("admin", f"context_admin_{timestamp}")
        
    asyncio.run(main())
