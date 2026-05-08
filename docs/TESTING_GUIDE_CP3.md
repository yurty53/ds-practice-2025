# Testing Guide - Checkpoint #3

## Prerequisites

Ensure the system is running with Docker Compose:

```bash
docker compose up
```

Make sure the following services are available:
- `payment`
- `executor1`, `executor2`, `executor3`
- `database1`, `database2`, `database3`

## Demo Scenarios

### 1. Happy Path

**Goal**: Show that the order is approved, the stock is decremented on the database replicas, and the payment is executed.

**What to show**:
- The Order Executor sends Prepare to the Books Database and Payment Service in parallel.
- Both services vote YES.
- The Order Executor sends Commit.
- The Books Database commits through quorum replication.
- The Payment Service logs the execution of the payment.

**Expected logs**:
- Database: `[2PC] Prepare VOTE_YES`
- Database: `[2PC] Commit SUCCESS`
- Payment: `[PREPARE] ✓ Voted YES for transaction ...`
- Payment: `[COMMIT] ✓ Payment executed for order ...`

### 2. Abort Path

**Goal**: Show that the system aborts safely when a participant votes NO.

**Ways to trigger**:
- Use an item with insufficient stock.
- Or force the payment participant to reject a Prepare request.

**What to show**:
- The Order Executor receives at least one NO vote.
- The Order Executor sends Abort.
- The Books Database releases the lock without committing.
- The Payment Service removes the staged transaction without executing it.

**Expected logs**:
- Database: `[2PC] Prepare VOTE_NO` or `[2PC] Abort`
- Database: lock released, no committed stock change
- Payment: `[ABORT] ✓ Payment aborted for order ...`

### 3. Recovery Path

**Goal**: Show the bonus recovery behavior of the Payment Service.

**Steps**:
1. Send a valid checkout so that the Payment Service receives Prepare.
2. Stop the payment container immediately after Prepare.
3. Start the payment container again.
4. Show the recovery logs.

**Expected logs**:
- Payment startup: `[RECOVERY] Loaded ... staged transactions from disk`
- Payment startup: `[RECOVERY] Transaction ... status=PREPARED ...`

### 4. Parallel Prepare Verification

**Goal**: Show that the coordinator sends Prepare concurrently to the database and payment participant.

**What to show**:
- Prepare requests appear close together in the logs.
- The system does not wait for the database to finish before contacting payment.

**Expected behavior**:
- Lower latency than a sequential Prepare flow.
- No unnecessary blocking between the two participants.

## Quick Validation Checklist

- [ ] Payment responds with `vote_yes=true` on valid Prepare.
- [ ] Payment logs `Payment executed for order X` on Commit.
- [ ] Payment logs `Payment aborted for order X` on Abort.
- [ ] Database Commit goes through quorum replication.
- [ ] Database lock is released on Abort.
- [ ] Restarting payment after Prepare shows recovery logs.

## Useful Commands

```bash
# Follow payment logs
docker compose logs -f payment

# Follow database logs
docker compose logs -f database1 database2 database3

# Follow executor logs
docker compose logs -f executor1 executor2 executor3
```

## Demo Order

1. Start with the happy path.
2. Repeat with an abort case.
3. Finish with the recovery demonstration.
