# Token Burn — Hackathon Prototype Plan

> **Constraint summary**: No model training. Hackathon-feasible. No clutter. Every feature must earn its place.

---

## What We Are Building

A middleware layer that sits between a client and any LLM backend, which:

1. **Demonstrates the exploit** — an attack simulator that drains an unprotected AI product's budget in real time
2. **Defends against it** — a rule-based, training-free defense pipeline
3. **Shows it working** — a live dashboard proving the defenses activate and the budget holds

The prototype wraps a **Mock LLM** (not a real API — no money spent, no API key needed, infinite scalability for the demo). The mock produces realistic token counts and latency. The defense layer is real and would work identically in front of a real LLM.

---

## What Is Explicitly OUT of Scope

Cut ruthlessly. These come from the design doc but are excluded here:

| Feature | Why Cut |
|---|---|
| Isolation Forest / any ML anomaly model | Requires training data and a training step |
| Historical regression for output estimation | Requires training data |
| Proxy model pre-flight (small model call) | Adds real API cost and latency; overkill |
| Cohort comparison (user vs. peer group) | Needs enough users to define cohorts; meaningless in a demo |
| Device fingerprinting / CAPTCHA challenges | Significant UX infrastructure; not the core insight |
| Tool call cascade cost attribution | Requires a full agentic framework; out of scope |
| Embedding/retrieval abuse defense | Niche attack vector; not visually compelling in a demo |
| Model routing (cheap → expensive model) | Requires multiple real model endpoints |
| Sliding window with Redis sorted sets | Fixed window is fine for a demo; sliding window is a production hardening detail |
| Streaming mid-stream abort | Provider billing semantics make this a half-defense anyway; skip |

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| **Backend** | Python + FastAPI | Fast to write, async-native, great for SSE |
| **Budget store** | Redis | Atomic `EVAL` / Lua for check-and-decrement; also drives pub/sub for dashboard |
| **Database** | SQLite (in-memory or file) | Request log storage; zero infrastructure |
| **Frontend** | Vanilla HTML + Chart.js | No build step, no framework overhead |
| **Dashboard push** | Server-Sent Events (SSE) over Redis Pub/Sub | Simpler than WebSocket; HTTP-native |
| **Attack simulator** | Python `asyncio` scripts | Concurrent attack scenarios, no external dependencies |
| **Mock LLM** | Python function | Returns token counts drawn from realistic distributions, adds configurable latency |

No Docker required. Everything runs with `pip install` + a local Redis instance (or `redis-server` in a terminal).

---

## System Components

### Component 1 — Mock LLM

A function (not a separate service) that simulates an LLM call. Called by the middleware after defenses pass.

**Behavior:**
- Accepts: `prompt_text`, `max_tokens`
- Computes: `input_tokens = len(prompt.split()) * 1.3` (rough but consistent)
- Generates: `output_tokens = min(max_tokens, sample from log-normal distribution)` seeded from prompt characteristics
- Adds: configurable latency (`asyncio.sleep(output_tokens * 0.02)` — 20ms per token, realistic)
- Returns: `{input_tokens, output_tokens, text: "...", latency_ms}`
- Cost: computed from a price table (GPT-4o rates by default, configurable)

**Why this earns its place**: Without a mock LLM, you either spend real money or can't run the attack simulator. With it, you can simulate 1,000 requests in seconds and show dramatic dashboard movement.

---

### Component 2 — Defense Middleware (FastAPI)

A single `/chat` endpoint. Every request passes through a sequential defense pipeline before hitting the Mock LLM.

**Pipeline steps (in order):**

```
POST /chat
    │
    ▼
[Step 1] Token Counter
    Count input tokens exactly.
    Flag if input_tokens > INPUT_TOKEN_LIMIT (configurable per tier).
    │
    ▼
[Step 2] Heuristic Complexity Scorer
    Score 0–10 based on rule-based signals.
    No training. Pure rules on the prompt text.
    │
    ▼
[Step 3] Cost Estimator + max_tokens Cap
    Compute dynamic max_tokens first, then derive cost_ceiling from it:
      budget_for_output = max(0, budget_remaining - input_tokens * P_in)
      budget_cap        = int(budget_for_output * 0.1 / P_out)
      max_tokens        = max(1, min(tier_cap, budget_cap))
      cost_ceiling      = input_tokens * P_in + max_tokens * P_out
    │
    ▼
[Step 4] Budget Gate (Redis atomic check)
    Atomically check + reserve cost_ceiling from user's budget.
    If insufficient: return 429 with budget_remaining and reset_time.
    │
    ▼
[Step 5] Velocity + Anomaly Check
    Check: cost_last_1h > VELOCITY_LIMIT
    Check: requests_last_10min > BURST_LIMIT
    Check: complexity_score > COMPLEXITY_THRESHOLD
    Action: if any triggered → apply policy (see below).
    │
    ▼
[Step 6] Mock LLM Call
    │
    ▼
[Step 7] Reconcile Budget
    Wrapped in try/finally — the refund MUST execute even if Step 6 raises an exception:
      try:
        result = mock_llm(prompt, max_tokens)
        actual_cost = input_tokens * P_in + result.output_tokens * P_out
        refund = cost_ceiling - actual_cost
        redis_add(user_id, refund)          # return unused ceiling
      except:
        redis_add(user_id, cost_ceiling)    # full refund on any failure
        raise
    Without this, a failed LLM call permanently removes cost_ceiling from the user's budget.
    │
    ▼
[Step 8] Log Request
    Write structured record to SQLite.
    Publish event to Redis pub/sub channel → dashboard.
    │
    ▼
Response to client
```

**Why this structure earns its place**: Every step solves a distinct attack vector. The order matters — cheap checks (token counting) happen before expensive ones. The reconciliation step (Step 7) is important: we reserve the ceiling upfront (prevents race conditions) and refund the unused portion (prevents over-charging legitimate users).

---

### Component 3 — Budget Engine (Redis-backed)

**Budget dimensions tracked per user** (three dimensions, not just cost):

```python
UserBudget:
    daily_cost_limit        # e.g. $2.00 for free tier
    per_request_cost_limit  # e.g. $0.50 max per single request
    daily_request_limit     # e.g. 50 requests/day
    hourly_request_limit    # e.g. 10 requests/hour (burst defense)
    daily_input_token_limit # e.g. 50K tokens/day (context stuffing defense)
```

**Why three dimensions and not just cost?**
- Cost-only: drained by a few expensive requests
- Request-count-only: blind to cost variance
- Input-token dimension: specifically catches context stuffing even if per-request cost hasn't spiked yet

**Storage**: Each dimension is a separate Redis key with TTL = 86400 seconds (resets daily).
Cost values are stored as **integers in microdollars** (multiply dollars by 1,000,000) to avoid
float precision issues — Redis DECRBY operates on integers only. Example: $2.00 → stored as
2,000,000. The Lua script checks all dimensions atomically before allowing the request.

**Tiers** (hardcoded in config, not in a DB):

| Tier | Daily Cost | Req/Hour | Req/Day | Max Tokens/Req | Input Tokens/Day |
|---|---|---|---|---|---|
| `free` | $2.00 | 10 | 50 | 512 | 50K |
| `pro` | $20.00 | 60 | 500 | 2048 | 500K |
| `admin` | Unlimited | Unlimited | Unlimited | 4096 | Unlimited |

The `admin` tier is used by the attack simulator to demonstrate the unprotected baseline.

---

### Component 4 — Heuristic Anomaly Scorer (no training)

A rule-based scorer that returns a risk score 0–10. No ML. No training data.

**Rules and weights:**

```
Input token count > 2000                                              → +3
Input token count > 5000                                              → +5 (cumulative)
Prompt contains 2+ of: "exhaustive / comprehensive / detailed /
  thorough / every possible / all possible / 10000"                   → +5
Prompt contains explicit length request
  ("write N words", "N pages")                                        → +4
Conversation turn > 15                                                → +2
Conversation turn > 30                                                → +4
Requests in last 10 min > 8                                           → +3
Cost in last 1 hour > 70% of daily limit                              → +3
Same prompt hash as recent request                                    → +5 (repetition attack)
```

**Policy mapping (no ML, just thresholds):**

| Score | Action |
|---|---|
| 0–4 | Allow normally |
| 5–6 | Reduce `max_tokens` by 50% |
| 7 | Reduce `max_tokens` to tier minimum (128); log warning |
| 8–9 | Soft deny — return a message explaining limit with retry-after |
| 10 | Hard deny — flag user as suspicious in Redis for 1 hour |

**Why this earns its place vs. Isolation Forest**: No training data, deterministic, interpretable. In a hackathon demo you want to be able to *explain* why a request was flagged. "Your prompt score was 8 because it contained an explicit length request and you've made 9 requests in the last 10 minutes" is demonstrable. An Isolation Forest score of -0.3 is not.

---

### Component 5 — Attack Simulator

Three attack scripts. Each targets a different vulnerability. Run against both the unprotected (admin tier) and protected (free tier) endpoints.

**Attack 1 — Naive Output Maximizer** (`attack_naive.py`)
```
Sends 50 requests with prompts like:
"Write an extremely detailed, comprehensive, and exhaustive analysis of [topic].
 Be as thorough as possible. Include every relevant detail."
Concurrency: 5 simultaneous requests.
```
*What it demonstrates*: Output maximization. The scorer fires at score=5 (2+ verbose keywords: "detailed", "comprehensive", "exhaustive", "thorough" all present) → max_tokens reduced from 512 to 256. After 10 requests the hourly limit fires. Total protected cost: ~$0.03 vs $2.05 unprotected.

**Attack 2 — Context Stuffer** (`attack_context.py`)
```
Sends requests with growing context:
Each request appends 1000 words (~1300 tokens) of filler to the prompt.
Request 8 has ~10,400 input tokens. Request 9 would reach ~58,500 cumulative tokens.
```
*What it demonstrates*: Two sequential defenses fire. At request 8, the scorer fires (input_tokens > 5000 → score=8 → soft deny). At request 9, the daily input token budget (50K) is exhausted and the budget gate denies. Both defenses are distinct: the scorer fires on single-request token size; the budget gate fires on cumulative daily consumption.

**Attack 3 — Sybil Burst** (`attack_sybil.py`)
```
Simulates 10 users (user_001 through user_010).
Each sends 15 requests rapidly.
Each user's requests 11–15 are blocked by the hourly limit (10/hour).
100 of 150 total requests succeed. 50 are denied.
Aggregate cost across all users: 100 × $0.00519 = ~$0.52.
```
*What it demonstrates*: Per-user budgets don't stop Sybil; shows the dashboard surfacing aggregate cost; motivates IP-level and org-level budget layers.

**Demo flow** (before/after):
1. Run Attack 1 against `admin` (unprotected) → watch cost explode on dashboard
2. Run Attack 1 against `free` (protected) → watch defenses activate, cost flatten
3. Run Attack 2 → watch scorer fire at request 8, then input token budget fire at request 9
4. Run Attack 3 → 10 of 15 requests per user succeed (hourly limit blocks the rest). Per-user gauges each hold at their limit. Aggregate cost on dashboard: ~$0.52. Point: per-user limits contain individual damage but do not bound aggregate cost across many accounts — motivates org-level or IP-level budget layers.
5. Toggle defenses off/on live (a single config flag) to show the contrast

---

### Component 6 — Live Spend Dashboard

Single HTML page. No framework. Refreshes via SSE push from the backend.

**Panels:**

| Panel | What It Shows | Update Trigger |
|---|---|---|
| **Global Spend Meter** | Total cost incurred (last 1h, 24h). Line chart, autoscaling. | Every request |
| **Spend Velocity** | $/minute rolling average. Red line at attack-level threshold. | Every request |
| **Budget Gauges** | Per-user remaining budget as a bar. Color: green → yellow → red. | Every request for that user |
| **Defense Action Feed** | Real-time log: "user_003 → DENIED (score: 8, reason: length request + burst)". Scrolling list. | Every defended request |
| **Request Heatmap** | Requests per user per minute. Highlights Sybil attack pattern. | Every 5 seconds |
| **Cost Breakdown** | Pie chart: % input cost vs. % output cost across all requests today. | Every 30 seconds |

**Implementation**: One `GET /dashboard/stream` SSE endpoint. Backend publishes a JSON event to Redis Pub/Sub on every request completion. The SSE endpoint subscribes and forwards to the browser. Chart.js handles all rendering.

**Why SSE over polling**: A dashboard that updates every 2 seconds via polling is fine. SSE is equally simple, adds no complexity, but the dashboard updates the *instant* a request arrives — visually far more compelling in a live demo. Worth the 30 minutes to implement over polling.

---

## Complete Data Flow

```
Attack Script
    │
    │ POST /chat {user_id, prompt, tier}
    ▼
FastAPI Middleware
    │
    ├─► [1] Token Counter → input_token_count
    │
    ├─► [2] Heuristic Scorer → complexity_score (0–10)
    │
    ├─► [3] max_tokens Cap = max(1, min(tier_cap, budget_cap))
    │
    ├─► [4] Redis: EVAL atomic_check_decrement
    │         ├── PASS → continue
    │         └── FAIL → return 429 immediately
    │
    ├─► [5] Velocity/Anomaly Check
    │         ├── score < 5 → allow
    │         ├── score 5–7 → reduce max_tokens further
    │         └── score 8+ → deny / flag
    │
    ├─► [6] Mock LLM(prompt, max_tokens)
    │         └── returns {input_tokens, output_tokens, latency_ms}
    │
    ├─► [7] Reconcile: try/finally refund (cost_ceiling - actual_cost) to Redis
    │         └── on exception: full refund of cost_ceiling
    │
    └─► [8] Log to SQLite
              └─► Publish to Redis Pub/Sub "events" channel
                          │
                          ▼
              SSE endpoint subscribes
                          │
                          ▼
              Browser Dashboard updates in real time
```

---

## Feature Inclusion Rationale (Final Judgment)

| Feature | In / Out | Reasoning |
|---|---|---|
| Mock LLM with realistic token distributions | **IN** | Essential; without it the demo costs real money |
| Input token counter (exact) | **IN** | Cheap, exact, catches context stuffing |
| `max_tokens` cap (dynamic, budget-aware) | **IN** | Single most effective defense; deterministic; zero estimation error |
| Multi-dimensional budgets (cost + requests + input tokens) | **IN** | Three dimensions catch three different attack types; still just Redis keys |
| Per-request cost ceiling + reconciliation | **IN** | Prevents race conditions AND prevents overcharging legit users |
| Heuristic complexity scorer (rule-based) | **IN** | No training; interpretable; enables graduated policy |
| Graduated policy (reduce → warn → deny) | **IN** | Shows nuance; binary block/allow is less impressive |
| Attack simulator (3 scenarios) | **IN** | The demo narrative lives here |
| SSE live dashboard | **IN** | Visual impact; proves the defenses work in real time |
| Request log (SQLite) | **IN** | Source of truth for dashboard; needed for velocity check |
| Velocity check (cost last 1h vs. threshold) | **IN** | Catches sustained attacks without ML; rule-based |
| Repetition detection (prompt hash) | **IN** | Trivial to implement (~5 lines); catches script-kiddie replay attacks |
| User flagging (1-hour Redis TTL ban) | **IN** | Closes the loop on score-10 events; shows the system acts, not just logs |
| Sliding window budgets | **OUT** | Fixed window is sufficient for demo; sliding window is a production detail |
| Isolation Forest / any ML | **OUT** | Requires training; violates constraint |
| Cohort comparison | **OUT** | Needs user population data |
| Device fingerprinting | **OUT** | Significant UX/infrastructure overhead |
| CAPTCHA | **OUT** | UX infrastructure; not the core insight |
| Model routing | **OUT** | Needs multiple real model endpoints |
| Streaming mid-stream abort | **OUT** | Provider billing semantics make it a half-defense |
| Tool call cost attribution | **OUT** | Needs full agentic framework |

---

## File Structure

```
token-burn/
├── backend/
│   ├── main.py              # FastAPI app, all routes
│   ├── mock_llm.py          # Mock LLM function
│   ├── defense/
│   │   ├── token_counter.py # Input token counting
│   │   ├── scorer.py        # Heuristic complexity scorer
│   │   ├── budget.py        # Redis budget engine (all dimensions)
│   │   └── policy.py        # Policy engine (score → action)
│   ├── logger.py            # SQLite logger + Redis pub/sub publisher
│   ├── dashboard.py         # SSE endpoint
│   └── config.py            # Tier limits, pricing table, thresholds
├── attacks/
│   ├── attack_naive.py      # Output maximizer
│   ├── attack_context.py    # Context stuffer
│   └── attack_sybil.py      # Sybil burst
├── frontend/
│   └── index.html           # Dashboard (Chart.js, SSE listener)
├── requirements.txt
└── README.md
```

---

## Time Estimate

| Component | Estimated Time |
|---|---|
| Mock LLM + config | 1.5h |
| Token counter + scorer + policy | 2h |
| Budget engine (Redis, all dimensions, atomic Lua) | 2.5h |
| FastAPI middleware (full pipeline) | 2.5h |
| SQLite logger + Redis pub/sub publisher | 1h |
| SSE endpoint + dashboard (Chart.js) | 3h |
| Attack simulator scripts (all 3) | 1.5h |
| Integration + end-to-end testing | 2h |
| Demo polish (config flags, reset scripts) | 1h |
| **Total** | **~17–18 hours** |

Comfortably within a 24-hour hackathon with a 2-person team. With 3 people, parallelizable: one person owns the backend pipeline, one owns the budget engine + Redis, one owns the dashboard + attack scripts.

---

## Demo Script (What Judges See)

1. **"Here is the problem"** — Run `attack_naive.py` against the unprotected endpoint. Dashboard shows cost spiking to $2.05 in under 2 minutes. Request log shows zero defenses firing.

2. **"Enable the defenses"** — Flip `DEFENSES_ENABLED = True` in `config.py` and restart the server.

3. **"Same attack, defended"** — Run `attack_naive.py` again. Dashboard shows cost flatlined. Defense Action Feed scrolls: "score: 5 (2+ verbose keywords matched) → max_tokens reduced to 256 → cost $0.003 instead of $0.041. After 10 requests, hourly limit fires. Total session cost: ~$0.03 vs $2.05 unprotected."

4. **"Different attack vector"** — Run `attack_context.py`. Scorer fires at request 8 (score=8, input tokens >5K). Input token budget fires at request 9 (cumulative >50K). Explain why input tokens are tracked as a separate budget dimension from cost.

5. **"Sybil gap"** — Run `attack_sybil.py`. Show per-user gauges all holding at the hourly limit wall. Show aggregate cost on the dashboard still reaching $0.52. "This is what per-user limits can't stop alone — it motivates IP-level or org-level budget layers." Honest engineering.

6. **"Reset and replay"** — Run `reset_budgets.py` (a 5-line Redis FLUSHDB script). Replay the attack. Show the cycle. System is stateless and restartable — important for a hackathon demo.
