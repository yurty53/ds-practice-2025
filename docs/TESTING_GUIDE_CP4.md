# Testing Guide - Checkpoint #4

## Prerequisites

```bash
docker compose down -v
docker compose up --build -d
# wait ~45 seconds for all containers to stabilise
```

Grafana: <http://localhost:3000> (admin / admin) → dashboard "Distributed Bookshop Stack - Signals Overview".

Locust: `pip install locust` (run from the repo root).

## Manual frontend walkthrough — S1 single non-fraudulent order

1. Open <http://localhost:8080> in a browser.
2. Add **Dune** to the cart from the index page.
3. Click **Checkout**.
4. Fill in the form (any name/email/billing). Card: `4111-1111-1111-1111`, expiry `12/28`, CVV `123`.
5. Submit. Confirmation page shows **Order Approved**.
6. In the terminal:
   ```bash
   docker compose logs orchestrator executor1 executor2 executor3 payment database1 --tail 80 | grep -E "checkout|PREPARE|COMMIT|2PC"
   ```
   Expected markers: `checkout_request`, `[PREPARE] ✓ Voted YES`, `[COMMIT] ✓ Payment executed`, `[2PC] Commit SUCCESS`.

## Automated suite — Locust

All commands run from the repo root.

### S1 — single non-fraudulent order

```bash
locust -f tests/locustfile.py --host http://localhost:8081 --headless -u 1 -r 1 -t 30s SingleOrderUser
```

Expected: 100% `[S1] Single non-fraudulent order` succeeds.

### S2 — multiple non-conflicting orders

```bash
locust -f tests/locustfile.py --host http://localhost:8081 --headless -u 8 -r 4 -t 60s NonConflictingUser
```

Expected: 100% approved; throughput visible in the summary.

### S3 — mixed fraud + non-fraud

```bash
locust -f tests/locustfile.py --host http://localhost:8081 --headless -u 6 -r 3 -t 60s MixedOrderUser
```

Expected: valid orders approved, fraudulent orders rejected. Ratio ≈ 3:1 valid:fraud.

### S4 — conflicting orders (same book)

```bash
locust -f tests/locustfile.py --host http://localhost:8081 --headless -u 10 -r 10 -t 30s ConflictingOrderUser
```

Expected: every request returns Approved or Rejected (no crashes). Watch panel 12 for prepare-vote "no" spikes.

## Bonus failure scenarios

### S5 — single leader failover

```bash
locust -f tests/locustfile.py --host http://localhost:8081 --headless -u 3 -r 3 -t 90s FailoverUser
```

Kills the current Bully leader 15s into the test, restarts it 35s in. Reports the wall-clock recovery time. Expected: < 10s typical.

### S6 — cascading leader failures

```bash
locust -f tests/locustfile.py --host http://localhost:8081 --headless -u 3 -r 3 -t 120s CascadingFailoverUser
```

Kills two leaders in sequence, restarts both at T+60s. Reports both re-election times.

### S7 — database quorum degradation

```bash
locust -f tests/locustfile.py --host http://localhost:8081 --headless -u 3 -r 3 -t 120s QuorumDegradationUser
```

Stops database3 at T+10s (quorum still met), stops database2 at T+35s (write quorum lost), restarts both at T+60s. Reports the recovery time.

## Telemetry walkthrough — what to watch in Grafana

For each test, open the dashboard `Distributed Bookshop Stack - Signals Overview` and watch the panels listed below. Expected shapes are described qualitatively; exact numbers vary with the load profile.

- **S1.** Panel 3 (Orders Processed) ticks up. Panel 1 + 2 (spans) show one trace per order across `orchestrator`, `txn-verification`, `fraud-detection`, `suggestions`, `order-queue`, `order-executor`, `database-replica`, `payment`. Panel 4 (Commits) ticks up alongside panel 3. Panel 7 (2PC latency p95) settles after a few requests.
- **S2.** Panel 3 throughput climbs proportionally to user count. Panel 5 (active prepared transactions) shows a small steady-state value.
- **S3.** Panel 11 (fraud rejection rate) climbs to ≈ 0.25. Panel 4 (commits) grows ~3x faster than panel 11 because valid:fraud is 3:1.
- **S4.** Panel 12 (prepare-vote split) shows occasional `db / no` votes — those are CAS conflicts that retry. Panel 7 latency p95 widens.
- **S5.** Panel 10 (alive executors) drops from 3 → 2 at the kill, back to 3 after restart. Panel 13 transitions counter increments to 1.
- **S6.** Panel 10 drops 3 → 2 → 1 → 3. Panel 13 transitions counter shows 2.
- **S7.** Panel 12 stacked timeseries shows `db / no` votes spike during the quorum-lost window. Panel 8 (quorum read latency) widens.

## Useful commands

```bash
# Follow logs across the 2PC participants
docker compose logs -f executor1 executor2 executor3 payment database1 database2 database3 \
  | grep -E "2PC|PREPARE|COMMIT|ABORT|RECOVERY"

# Reset Grafana state without losing the dashboard JSON
docker compose restart observability grafana-importer

# Re-import the dashboard JSON after editing it
docker compose restart grafana-importer
```

## Quick validation checklist

- [ ] All four S14 scenarios (S1-S4) report 0 failures with the commands above.
- [ ] Bonus scenarios (S5-S7) report recovery times in the end-of-test summary.
- [ ] Every dashboard panel 1-13 has data after running `MixedOrderUser` for 60s.
- [ ] Tempo Explore shows traces for every service in the Telemetry Summary table of `ARCHITECTURE_CP4.md`.
