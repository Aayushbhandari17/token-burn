import asyncio
import aiohttp
import time

URL = "http://127.0.0.1:8000/chat"
PROMPT = "Quick simple query."

async def send_request(session, user_id):
    payload = {
        "user_id": user_id,
        "tier": "free",
        "prompt": PROMPT
    }
    try:
        async with session.post(URL, json=payload) as response:
            status = response.status
            data = await response.json()
            return status, data
    except Exception as e:
        return 500, {"detail": str(e)}

async def run_sybil_attack():
    print("\n--- Running Sybil Burst Attack ---")
    timestamp = int(time.time())
    users = [f"sybil_{timestamp}_{i:03d}" for i in range(10)]
    requests_per_user = 15
    
    total_allowed = 0
    total_denied = 0
    aggregate_cost = 0.0
    user_results = {u: {"allowed": 0, "denied": 0, "cost": 0.0} for u in users}
    
    async with aiohttp.ClientSession() as session:
        # Launch concurrently
        tasks = []
        for i in range(requests_per_user):
            for user_id in users:
                tasks.append((user_id, send_request(session, user_id)))
                
        # Actually run them
        results = await asyncio.gather(*(t[1] for t in tasks))
        
        for (user_id, _), (status, data) in zip(tasks, results):
            if status == 200:
                total_allowed += 1
                user_results[user_id]["allowed"] += 1
                
                cost = data.get("cost", {}).get("total_cost", 0.0)
                aggregate_cost += cost
                user_results[user_id]["cost"] += cost
            else:
                total_denied += 1
                user_results[user_id]["denied"] += 1

    print(f"Total Users: {len(users)}")
    print(f"Requests per user: {requests_per_user}")
    print(f"\n--- Aggregate Results ---")
    print(f"Total Allowed: {total_allowed}")
    print(f"Total Denied: {total_denied}")
    print(f"Aggregate Cost: ${aggregate_cost:.4f}")
    
    print(f"\n--- Per-User Breakdown ---")
    # Just show a sample (first 3) to not spam the terminal
    for u in users[:3]:
        print(f"{u}: {user_results[u]['allowed']} allowed, {user_results[u]['denied']} denied, cost ${user_results[u]['cost']:>5.4f}")
    print(f"... and {len(users)-3} more users with similar patterns.")

if __name__ == "__main__":
    asyncio.run(run_sybil_attack())
