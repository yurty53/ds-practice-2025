# Testing Guide - Checkpoint #2

## Prerequisites

Ensure Docker and Docker Compose are installed and the system is running:
```bash
docker compose up
```

## Test Scenarios

### 1. Successful Order with Flow (End-to-End)

**Scenario**: Valid checkout leading to asynchronous execution.

**Request**:
```bash
curl -X POST http://localhost:8081/checkout \
  -H "Content-Type: application/json" \
  -d '{
    "user_name": "User",
    "user_contact": "user@example.com",
    "creditCard": {"number": "1234567890123456"},
    "items": [{"name": "Dune", "quantity": 1}]
  }'
```

**Expected Results**:

**Response:** {"status": "Order Approved", "orderId": "...", "suggestedBooks": [...], "vectorClock": {...}}

**Orchestrator Logs:** [INFO] Enqueued order <ID> | queue size: 1

**Order Queue Logs:** [INFO] Enqueue called | order_id: <ID> | queue size: 1

**Executor Logs (Leader only):** [INFO] Dequeued order <ID> | Executing for User: 1x Dune

---

### 2. Leader Election & Fault Tolerance (The Bully Test)

**Scenario**: Verify that the system self-heals when a leader crashes.

**1.Identify Leader:** Check logs to see which executor is calling Dequeue (e.g., executor3).

**2.Kill Leader:**
```bash

docker stop executor3
```
**3.Observe Re-election:**

- Follower Logs (executor2): [WARNING] Leader heartbeat failed. Starting election...

- Election Result: executor2 (highest remaining ID) should log [INFO] I am the new leader.

**4.Resume:** Perform a checkout; executor2 should now be the one processing the order.

---

### 3. Causal Consistency (Vector Clocks & ClearOrder)

**Scenario**: Verify that the causal history is correctly tracked and cleaned up.

**Expected Logs**:

**Verification:** Check that each service logs a Vector Clock reflecting the history of previous calls (e.g., Suggestions receiving VC: {TX:3, FD:2}).

**Cleanup:** After checkout, look for:

- Orchestrator: [INFO] Broadcasting ClearOrder with VCf: {...}

- Services: [INFO] ClearOrder received. Local VC matches. Cleaning state for order <ID>.

---

### 4. Fraud & Rejection (No Enqueue)

**Scenario**: Ensure rejected orders never reach the execution queue.

**Request**:

Request (Fraud card):
```bash
curl -X POST http://localhost:8081/checkout \
  -H "Content-Type: application/json" \
  -d '{
    "creditCard": {"number": "1234-5678-9012-3456"},
    "items": [{"name": "Dune", "quantity": 1}]
  }'
```
Expected Results:

- Response: status: "Order Rejected"

- Order Queue Logs: Should NOT show any new Enqueue calls.

- Executor Logs: Should NOT process any new orders.

---

### 5. Testing Mutual Exclusion

To verify that only one executor processes the queue:

1. Observe order_queue logs.

2. You should see Dequeue called messages coming from only one IP/Container Name (the Leader) every 2 seconds.

3. Even if you have 3 executors running, the queue size should never decrease by more than 1 per operation, proving the threading.Lock() is working.

---

### Verifying Deployment (Checkpoint #2 Specs)

Check that all 8 containers are running:

```bash
  docker compose ps
```

Expected Output:
```
NAME                          STATUS
frontend-1                    running
orchestrator-1                running
fraud_detection-1             running
transaction_verification-1    running
suggestions-1                 running
order_queue-1                 running
executor1                     running
executor2                     running
executor3                     running (Bonus)
```

---

## Troubleshooting & Logs

Follow specific service interactions:

```bash
# To watch the election happen:
docker compose logs -f executor1 executor2 executor3

# To watch the order flow:
docker compose logs -f orchestrator order_queue executor2
```

Inspect Vector Clock issues:

If a service logs [ERROR] Cannot clear order: local clock is ahead of VCf, it means a message was reordered or a service participated in a call the orchestrator didn't track.

---

## Performance Benchmark

Parallel vs Sequential:

- Parallel Verification: Response time should stay around max(T_fraud, T_trans, T_sugg) + T_enqueue.

- Since execution is now asynchronous, the total response time for the user is significantly faster than in Checkpoint #1, as it no longer includes the "order processing" time.
