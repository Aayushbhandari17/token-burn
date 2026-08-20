#!/bin/bash
# 1. Enable defenses
sed -i '' 's/DEFENSES_ENABLED = False/DEFENSES_ENABLED = True/g' backend/config.py

# 2. Reset database and Redis
python3 scripts/reset.py

# 3. Restart server
pkill -f uvicorn
uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
sleep 2

# 4. Run tests
echo "=== Running Naive Attack (Protected) ==="
python3 attacks/attack_naive.py
echo "=== Running Context Attack ==="
python3 attacks/attack_context.py
echo "=== Running Sybil Attack ==="
python3 attacks/attack_sybil.py
echo "=== Running Manual Request ==="
curl -s -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": "manual_user_1", "tier": "free", "prompt": "This is a normal manual test."}'
echo ""
