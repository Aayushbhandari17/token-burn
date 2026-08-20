# Token Burn Defense Prototype — Phase 1 Documentation

This document outlines the architecture, components, and current status of the Token Burn Defense Prototype. The goal of the project is to build a defense framework against **Token Burn Attacks** (malicious prompt engineering or behavior designed to inflate LLM API usage costs and consume rate limits).

---

## 1. Project Architecture Overview

The system is a prototype API backend built using **FastAPI** and simulated dependencies. It consists of the following key directories and files:

- `backend/`: Core codebase directory.
  - [`main.py`](file:///c:/Users/diaar/Downloads/token-burn/backend/main.py): Contains the FastAPI entry point and `/chat` endpoint logic, including basic unit tests.
  - [`config.py`](file:///c:/Users/diaar/Downloads/token-burn/backend/config.py): Defines pricing models, tier limits, heuristic rules, and defense thresholds.
  - [`mock_llm.py`](file:///c:/Users/diaar/Downloads/token-burn/backend/mock_llm.py): Simulates real LLM responses, counting input/output tokens, estimating costs, and injecting realistic latency.
  - `defense/`: A placeholder module meant for future implementation of defense rules (currently only contains `__init__.py`).
- `requirements.txt`: Python package dependencies (`fastapi`, `uvicorn`, `redis`, `sse-starlette`).

---

## 2. Component Analysis

### A. Configuration System (`config.py`)
The configuration file establishes the baseline for the application's cost modeling and defense triggers:

1. **Defenses Toggle**:
   - `DEFENSES_ENABLED`: A boolean switch to enable or disable defense behaviors globally for demonstration comparison.
2. **Pricing Constants**:
   - Simulated GPT-4o-mini scale costs:
     - Input token price: `$0.0000025` per token
     - Output token price: `$0.0000100` per token
3. **Budget Tiers (`TIERS`)**:
   - **`free`**: `$2.00` daily cost limit, 10 req/hour, 50 req/day, 512 max output tokens per request, 50k input tokens/day limit.
   - **`pro`**: `$20.00` daily cost limit, 60 req/hour, 500 req/day, 2048 max output tokens per request, 500k input tokens/day limit.
   - **`admin`**: Infinite daily budget and requests, but capped at 4,096 max output tokens per request.
4. **Heuristic & Policy Rules**:
   - Rules scoring system based on input size, repetitive behavior, verbose request keywords (like `"exhaustive"`, `"detailed"`, `"comprehensive"`, `"10000"` words), and burst patterns.
   - Thresholds define progressive mitigation actions:
     - **Score 5-6**: Reduce `max_tokens` by 50%
     - **Score 7**: Reduce `max_tokens` to minimum limit (128)
     - **Score 8-9**: Soft deny (suggest retry later)
     - **Score 10+**: Hard deny (1-hour IP/user ban via Redis)

### B. Mock LLM Simulator (`mock_llm.py`)
Simulates calling a real LLM using local rules to mimic token generation behavior:
- **Input Tokens**: Estimated as `len(prompt_words) * 1.3`.
- **Output Target**: Based on prompt parameters. If a token burn attack indicator is present (e.g. `"10000"` or `"exhaustive"`), it triggers a default output target of `4000` tokens.
- **Latency Emulation**: Emulates generation time based on simulated output tokens (defaults to 20ms per token, adjustable for tests).
- **Cost Calculation**: Computes aggregate billing metrics for input and output.

### C. Web Server & Routing (`main.py`)
- Employs FastAPI with a `/chat` endpoint.
- Validates that the requested tier matches the configured tiers.
- Defaults the `max_tokens` limit based on the user's tier if none is provided in the payload.
- Passes the request directly to the Mock LLM.

---

## 3. Current Implementation Status (What has been done)

1. **Mock Environment Ready**: The mock LLM accurately simulates pricing, outputs, and latencies.
2. **Configuration Declared**: Rules, tiers, thresholds, and pricing constants are defined in `config.py`.
3. **Endpoint Validation**: The `/chat` endpoint verifies basic parameters (like tier name) and routes the request payload.
4. **Basic Tests Executed**: Unit tests are embedded within the `if __name__ == "__main__":` block of `main.py` to assert that the router, tier validation, and mock LLM integration are working as expected without defenses.
5. **No Defense Logic Yet**: The defense layers (heuristics scoring, Redis rate-limiting/burst-limiting, policy actions) are not yet integrated or implemented in the actual endpoint handler. All requests are currently bypassed to the Mock LLM.
