# Token Burn — Comprehensive Frontend Architectural Plan

This document provides a highly detailed specification of the Token Burn web interfaces: the **Interactive Chat Interface** (`frontend/chat.html`) and the **Live Spend Security Dashboard** (`frontend/dashboard.html`).

---

## 1. Technical Stack & Dependencies

* **HTML5**: Standard markup with semantic containers for grid layouts and scroll feeds.
* **Vanilla CSS3**: Tailored styling using system font stacks, flexible grids, box shadows, and responsive layouts. Includes state-specific color rules (`.blocked`, `.allowed`).
* **Chart.js (v4.x via CDN)**: Used for responsive data visualization. Instantiated with animations disabled or tuned for real-time rendering performance.
* **Server-Sent Events (SSE)**: Built-in browser `EventSource` API handles incoming streams over HTTP without WebSockets or polling cycles.

---

## 2. Page 1: Chat Client (`frontend/chat.html`)

An interface mimicking a typical AI chatbot to allow manual input and testing of defense pipelines.

### Key Functional Features:
1. **User Identity Configurator**: Input boxes for user ID and dropdowns to select the budget tier (`free`, `pro`, `admin`).
2. **Interactive Chat Input**: Text area for user prompts and a Send button.
3. **Response Area**: Renders the generated Mock LLM text on success, or visual warnings (such as red-bordered error cards) on rate limits, budget exhaustion, or risk flags.
4. **Real-time Pipeline Statistics**:
   - Displays latency in milliseconds.
   - Displays actual transaction cost.
   - Shows calculated input and output token counts.
   - Lists pipeline diagnostics (`_debug_score`, `_debug_action`, `_debug_reasons`).

---

## 3. Page 2: Live Spend Security Dashboard (`frontend/dashboard.html`)

A monitoring station composed of 6 grid-aligned panels subscribing to the `/dashboard/stream` endpoint.

### Detailed Panel Specifications:

#### A. Global Spend Meter
* **Component**: Large text status container (`#global-spend`).
* **State**: Tracks a single cumulative floating-point value: `totalSpend`.
* **Logic**: On every event, parses `actual_cost` and increments `totalSpend`. Displays formatted micro-dollars (e.g., `$0.0051`).

#### B. Spend Velocity
* **Component**: Chart.js Line Chart (`#velocityChart`).
* **Rolling window**: Uses a background `setInterval` running every 2 seconds to prune events older than 60 seconds (`recentCosts`).
* **Calculations**: Sums the cost of requests within the rolling 1-minute window to derive a rolling `$/minute` rate.
* **Attack Threshold**: Renders a static red dashed horizontal line (`#f44336`) at `$0.0233 / minute` to flag output-maximizing behavior.

#### C. Cost Breakdown
* **Component**: Chart.js Doughnut Chart (`#breakdownChart`).
* **Logic**: Computes:
  - `Input Cost = Input Tokens * $0.0000025` (GPTo pricing rate).
  - `Output Cost = Total Cost - Input Cost`.
* **Proportioning**: Displays a cumulative ratio of aggregate input token costs (purple) vs. output token costs (amber) today.

#### D. Request Heatmap (Requests/User/Min)
* **Component**: Chart.js Stacked Bar Chart (`#heatmapChart`).
* **Aggregation**: Groups and counts request volumes by `user_id` and grouping intervals of 1 minute (`HH:MM`).
* **Visuals**: Uses unique colors per user dynamically derived from their user ID string (via a string-hashing algorithm in JS) to stack usage metrics.

#### E. Per-user Budget Gauges
* **Component**: Sorted list of scrollable feed items (`#user-gauges`).
* **Logic**: Maintains a dictionary mapping `user_id` to total spent. Sorts the list descending by cost to bubble heavy spenders to the top.

#### F. Defense Action Feed
* **Component**: Scrolling log feed (`#action-feed`) capped at the last 50 events.
* **Color States**:
  - Green Left Border (`.allowed`): Applied to normal success transactions or `REDUCE_50`/`REDUCE_MIN` actions.
  - Red Left Border (`.blocked`): Applied to `SOFT_DENY`, `HARD_DENY`, or budget failures.
* **Meta info**: Lists timestamp, user ID, calculated risk score, policy action, cost, and exact token splits.

---

## 4. End-to-End Real-Time Event Pipeline

```
  [ FastAPI POST /chat ]
             │ (logs transaction / publishes event)
             ▼
      [ Redis Pub/Sub ]
             │ (channel: "events")
             ▼
  [ GET /dashboard/stream ]
             │ (Server-Sent Events)
             ▼
   [ Browser EventSource ]
             │
             ├─► 1. Parse JSON: timestamp, user_id, status, risk_score, actual_cost, etc.
             │
             ├─► 2. Update Global Spend: totalSpend += actual_cost
             │
             ├─► 3. Track Velocity: push {timeMs, cost} to recentCosts array
             │
             ├─► 4. Calculate Split: Input Cost = input_tokens * 0.0000025; Output Cost = actual_cost - Input Cost
             │
             ├─► 5. Group Heatmap: increment user_id counter for active minute string
             │
             ├─► 6. Sort Gauges: update user dictionary, sort descending, rebuild HTML
             │
             └─► 7. Prepend Action Feed: create div, style border, insert detail text
```
