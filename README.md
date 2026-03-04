# Distributed Systems @ University of Tartu

This repository contains the implementation of a distributed bookstore system for the Distributed Systems course at the University of Tartu.

## Documentation

- **[Architecture](docs/ARCHITECTURE.md)** - System architecture and design
- **[Testing Guide](docs/TESTING_GUIDE.md)** - Test scenarios
- **[Utils](utils/README.md)** - Protocol Buffer specifications

## System Architecture

The system consists of 4 microservices communicating via REST and gRPC protocols:

### Services Overview

1. **Frontend** (Port 8080)
   - Static HTML/JavaScript interface
   - Communicates with orchestrator via REST API

2. **Orchestrator** (Port 8081)
   - REST API server (Flask)
   - Coordinates communication between gRPC services
   - Implements parallel processing using threading
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

### Communication Protocols

- **Frontend ↔ Orchestrator**: REST (HTTP/JSON)
- **Orchestrator ↔ Backend Services**: gRPC (Protocol Buffers)

### Key Features

- **Parallel Processing**: Orchestrator uses threading to call all 3 gRPC services concurrently
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
