# Distributed Systems @ University of Tartu

This repository contains the implementation of a distributed bookstore system for the Distributed Systems course at the University of Tartu.

## Documentation

- **[Architecture CP1](docs/ARCHITECTURE_CP1.md)** - System architecture and design (Checkpoint #1)
- **[Testing Guide CP1](docs/TESTING_GUIDE_CP1.md)** - Test scenarios (Checkpoint #1)
- **[Architecture CP2](docs/ARCHITECTURE_CP2.md)** - System architecture and design (Checkpoint #2)
- **[Testing Guide CP2](docs/TESTING_GUIDE_CP2.md)** - Test scenarios (Checkpoint #2)
- **[Utils](utils/README.md)** - Protocol Buffer specifications

## System Architecture

The system consists of 9 service instances communicating via REST and gRPC protocols:

### Services Overview

1. **Frontend** (Port 8080 -> container 80)
   - Static HTML/JavaScript interface
   - Communicates with orchestrator via REST API

2. **Orchestrator** (Port 8081 -> container 5000)
   - REST API server (Flask)
   - Coordinates communication between gRPC services
   - Handles synchronous verification and asynchronous enqueue
   - Endpoints: `/checkout`, `/suggestions`

3. **Fraud Detection** (Port 50051)
   - gRPC server
   - Validates credit card transactions for fraud
   - Flags suspicious card numbers

4. **Transaction Verification** (Port 50052)
   - gRPC server
   - Validates transaction format and content
   - Checks card number format and order items

5. **Suggestions** (Port 50053)
   - gRPC server
   - Provides book recommendations based on cart contents
   - Uses genre-based matching algorithm

6. **Order Queue** (Port 50054)
   - gRPC FIFO queue for approved orders
   - Receives `Enqueue` from orchestrator
   - Serves `Dequeue` to the current executor leader

7. **Executor 1** (Host 50055 -> container 50050)
   - Order execution replica
   - Participates in Bully election and heartbeat checks

8. **Executor 2** (Host 50056 -> container 50050)
   - Order execution replica
   - Participates in Bully election and heartbeat checks

9. **Executor 3** (Host 50057 -> container 50050)
   - Order execution replica
   - Participates in Bully election and heartbeat checks

### Communication Protocols

- **Frontend ↔ Orchestrator**: REST (HTTP/JSON)
- **Orchestrator ↔ Backend Services**: gRPC (Protocol Buffers)

### Key Features

- **Decoupled Flow**: Verification is synchronous, execution is asynchronous via `order_queue`
- **Vector Clocks**: Causal ordering is tracked across events `a` to `f`
- **Causal Cleanup**: `ClearOrder` broadcast with final vector clock (`VCf`) clears per-order temporary state safely
- **Bully Leader Election**: 3 executors elect the highest alive ID as leader for queue consumption
- **Heartbeat Fault Detection**: Followers detect leader crashes and trigger re-election automatically
- **Logging**: Comprehensive logging across all services for debugging and monitoring
- **Docker Compose**: All services containerized and orchestrated
- **Hot Reload**: Code changes automatically reflected without manual restart

## Getting started

### Overview

The code consists of multiple services. Each service is located in a separate folder. The `frontend` service folder contains a Dockerfile and the code for an example bookstore application. Each backend service folder (e.g. `orchestrator` or `fraud_detection`) contains a Dockerfile, a requirements.txt file and the source code of the service.

There is also a `utils` folder that contains some helper code or specifications that are used by multiple services. Check the `utils` folder for more information.

### Running the code with Docker Compose [recommended]

To run the code, you need to clone this repository, make sure you have Docker and Docker Compose installed, and run the following command in the root folder of the repository:

```bash
docker compose up
```

This will start the system with the multiple services. Each service will be restarted automatically when you make changes to the code, so you don't have to restart the system manually while developing. If you want to know how the services are started and configured, check the `docker-compose.yaml` file.

The checkpoint evaluations will be done using the code that is started with Docker Compose, so make sure that your code works with Docker Compose.

If, for some reason, changes to the code are not reflected, try to force rebuilding the Docker images with the following command:

```bash
docker compose up --build
```

### Testing the System

Once the system is running, you can:

1. **Access the frontend**: Open http://localhost:8080 in your browser
2. **Test the REST API**: Use curl or Postman to send requests to http://localhost:8081
3. **View logs**: Check Docker Compose output for system logs from all services

Example curl command to test checkout:
```bash
curl -X POST http://localhost:8081/checkout \
  -H "Content-Type: application/json" \
  -d '{
    "creditCard": {"number": "1234567890123456"},
    "items": [{"name": "Dune", "quantity": 1}]
  }'
```

## Documentation

See [docs/](docs/) folder for detailed documentation.

### Run the code locally

Even though you can run the code locally, it is recommended to use Docker and Docker Compose to run the code. This way you don't have to install any dependencies locally and you can easily run the code on any platform.

If you want to run the code locally, you need to install the following dependencies:

backend services:
- Python 3.8 or newer
- pip
- [grpcio-tools](https://grpc.io/docs/languages/python/quickstart/)
- requirements.txt dependencies from each service

frontend service:
- It's a simple static HTML page, you can open `frontend/src/index.html` in your browser.

And then run each service individually.
