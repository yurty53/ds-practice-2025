# Testing Guide

## Prerequisites

Ensure Docker and Docker Compose are installed and the system is running:
```bash
docker compose up
```

## Test Scenarios

### 1. Successful Order with Suggestions

**Scenario**: Valid card, valid items, get recommendations

**Request**:
```bash
curl -X POST http://localhost:8081/checkout \
  -H "Content-Type: application/json" \
  -d '{
    "creditCard": {"number": "1234567890123456"},
    "items": [
      {"name": "Dune", "quantity": 1},
      {"name": "Foundation", "quantity": 1}
    ]
  }'
```

**Expected Response**:
```json
{
  "orderId": "12345",
  "status": "Order Approved",
  "suggestedBooks": [
    {"title": "Neuromancer", "author": "William Gibson"}
  ]
}
```

**Expected Logs**:
```
orchestrator    | [INFO] Checkout request received | items: [...]
orchestrator    | [INFO] Calling fraud detection | card: 1234567890123456
orchestrator    | [INFO] Calling transaction verification | card: 1234567890123456
orchestrator    | [INFO] Calling suggestions service | titles: ['Dune', 'Foundation']
fraud_detection | [INFO] Received request | card: 1234567890123456 | amount: 2
fraud_detection | [INFO] No fraud detected
transaction_verification | [INFO] Received request | card: 1234567890123456
transaction_verification | [INFO] Transaction verified successfully
suggestions     | [INFO] GetSuggestions called | cart: ['Dune', 'Foundation']
suggestions     | [INFO] Detected genres: {'Sci-Fi'}
suggestions     | [INFO] Returning 1 suggestions
orchestrator    | [INFO] All services responded | fraud=False | valid=True
orchestrator    | [INFO] Order approved
```

---

### 2. Fraudulent Card Detection

**Scenario**: Test fraud card should be rejected

**Request**:
```bash
curl -X POST http://localhost:8081/checkout \
  -H "Content-Type: application/json" \
  -d '{
    "creditCard": {"number": "1234-5678-9012-3456"},
    "items": [{"name": "Dune", "quantity": 1}]
  }'
```

**Expected Response**:
```json
{
  "orderId": "12345",
  "status": "Order Rejected",
  "suggestedBooks": []
}
```

**Expected Logs**:
```
fraud_detection | [WARNING] Fraud detected: test card number flagged
orchestrator    | [INFO] All services responded | fraud=True | valid=False
```

---

### 3. Invalid Card Format

**Scenario**: Card number not 16 digits

**Request**:
```bash
curl -X POST http://localhost:8081/checkout \
  -H "Content-Type: application/json" \
  -d '{
    "creditCard": {"number": "123"},
    "items": [{"name": "Dune", "quantity": 1}]
  }'
```

**Expected Response**:
```json
{
  "orderId": "12345",
  "status": "Order Rejected",
  "suggestedBooks": []
}
```

**Expected Logs**:
```
transaction_verification | [WARNING] Transaction rejected: Invalid card number format
orchestrator    | [INFO] All services responded | fraud=False | valid=False
```

---

### 4. Empty Order

**Scenario**: No items in cart

**Request**:
```bash
curl -X POST http://localhost:8081/checkout \
  -H "Content-Type: application/json" \
  -d '{
    "creditCard": {"number": "1234567890123456"},
    "items": []
  }'
```

**Expected Response**:
```json
{
  "orderId": "12345",
  "status": "Order Rejected",
  "suggestedBooks": []
}
```

**Expected Logs**:
```
transaction_verification | [WARNING] Transaction rejected: No items in order
orchestrator    | [INFO] All services responded | fraud=False | valid=False
```

---

### 5. Suggestions Only

**Scenario**: Get recommendations without checkout

**Request**:
```bash
curl -X POST http://localhost:8081/suggestions \
  -H "Content-Type: application/json" \
  -d '{
    "book_titles": ["Dune", "Foundation"]
  }'
```

**Expected Response**:
```json
{
  "suggestions": [
    {"title": "Neuromancer", "author": "William Gibson"}
  ]
}
```

**Expected Logs**:
```
suggestions | [INFO] GetSuggestions called | cart: ['Dune', 'Foundation']
suggestions | [INFO] Detected genres: {'Sci-Fi'}
suggestions | [INFO] Returning 1 suggestions
```

---

### 6. Multiple Genres

**Scenario**: Books from different genres get suggestions from all genres

**Request**:
```bash
curl -X POST http://localhost:8081/suggestions \
  -H "Content-Type: application/json" \
  -d '{
    "book_titles": ["Dune", "Lord of the Rings", "Brothers Karamazov"]
  }'
```

**Expected Response**: Suggestions from Sci-Fi, Fantasy, and Classic Literature genres

**Expected Logs**:
```
suggestions | [INFO] Detected genres: {'Sci-Fi', 'Fantasy', 'Classic Literature'}
suggestions | [INFO] Returning 6 suggestions
```

---

## Testing Threading

To verify parallel execution, check the timestamps in logs. All three services should be called at approximately the same time:

```
2024-01-15 10:30:45.123 [INFO] Calling fraud detection
2024-01-15 10:30:45.124 [INFO] Calling transaction verification
2024-01-15 10:30:45.125 [INFO] Calling suggestions service
```

The timestamps should be within milliseconds of each other, proving parallel execution.

---

## Testing with Frontend

1. Open http://localhost:8080 in browser
2. Add books to cart (e.g., "Dune", "Foundation")
3. Click checkout
4. Enter card number:
   - Valid: `1234567890123456`
   - Fraud: `1234-5678-9012-3456`
   - Invalid: `123`
5. Observe order status and suggestions

---

## Verifying gRPC Communication

Check that all gRPC services are running:

```bash
docker compose ps
```

Expected output:
```
NAME                          STATUS
distributed-fraud_detection-1        running
distributed-orchestrator-1           running
distributed-suggestions-1            running
distributed-transaction_verification-1  running
distributed-frontend-1               running
```

---

## Debugging Tips

### View logs for specific service:
```bash
docker compose logs -f orchestrator
docker compose logs -f fraud_detection
docker compose logs -f transaction_verification
docker compose logs -f suggestions
```

### Restart specific service:
```bash
docker compose restart orchestrator
```

### Rebuild after code changes:
```bash
docker compose up --build
```

### Check service health:
```bash
curl http://localhost:8081/
```

---

## Performance Testing

### Measure response time:
```bash
time curl -X POST http://localhost:8081/checkout \
  -H "Content-Type: application/json" \
  -d '{
    "creditCard": {"number": "1234567890123456"},
    "items": [{"name": "Dune", "quantity": 1}]
  }'
```

### Expected response time:
- With threading (parallel): ~100-200ms
- Without threading (sequential): ~300-600ms

The parallel implementation should be significantly faster.

---

## Test Data

### Valid Card Numbers (16 digits):
- `1234567890123456`
- `9876543210987654`
- `1111222233334444`

### Fraudulent Card (test):
- `1234-5678-9012-3456`

### Invalid Card Numbers:
- `123` (too short)
- `12345678901234567` (too long)
- `abcd1234efgh5678` (contains letters)

### Available Books by Genre:

**Sci-Fi**:
- Dune
- Neuromancer
- Foundation

**Fantasy**:
- Lord of the Rings
- The Name of the Wind
- The Chronicles of Narnia

**Classic Literature**:
- Brothers Karamazov
- Les Misérables
- The Iliad

**Magical Realism**:
- 100 Years of Solitude
- Feast of the Goat
- The House of Spirits

**Literary Fiction**:
- Midnight's Children
- God of Small Things
- The Grapes of Wrath
