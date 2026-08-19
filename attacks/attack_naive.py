import asyncio
import aiohttp
import time

URL = "http://127.0.0.1:8000/chat"
VERBOSE_PROMPT = "Write an extremely detailed, comprehensive, and exhaustive analysis of AI. Be as thorough as possible. Include every relevant detail."

async def send_request(session, user_id, tier, max_tokens=None):
    payload = {
        "user_id": user_id,
        "tier": tier,
        "prompt": VERBOSE_PROMPT
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
        
    try:
        async with session.post(URL, json=payload) as response:
            status = response.status
            data = await response.json()
            return status, data
    except Exception as e:
        return 500, {"detail": str(e)}

async def run_attack(tier, user_id, num_requests=50, concurrency=5):
    print(f"\n--- Running Naive Attack on '{tier}' tier ---")
    allowed = 0
    denied = 0
    total_cost = 0.0
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for i in range(num_requests):
            tasks.append(send_request(session, user_id, tier))
            if len(tasks) == concurrency:
                results = await asyncio.gather(*tasks)
                for status, data in results:
                    if status == 200:
                        allowed += 1
                        total_cost += data.get("cost", {}).get("total_cost", 0.0)
                    else:
                        denied += 1
                tasks = []
        
        # Flush remaining tasks
        if tasks:
            results = await asyncio.gather(*tasks)
            for status, data in results:
                if status == 200:
                    allowed += 1
                    total_cost += data.get("cost", {}).get("total_cost", 0.0)
                else:
                    denied += 1
                    
    print(f"Tier: {tier}")
    print(f"Total Attempted: {num_requests}")
    print(f"Allowed: {allowed}")
    print(f"Denied: {denied}")
    print(f"Total Cost: ${total_cost:.4f}")

if __name__ == "__main__":
    async def main():
        # Clean redis state for users first if possible, but attack script shouldn't bypass defense components.
        # We'll just use unique user IDs for a fresh run
        timestamp = int(time.time())
        await run_attack("free", f"naive_free_{timestamp}")
        await run_attack("admin", f"naive_admin_{timestamp}")
        
    asyncio.run(main())
