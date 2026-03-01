# Use the same base image as the other services
FROM python:3.11

# Set the working directory
WORKDIR /app

# Install dependencies
RUN pip install grpcio grpcio-tools

# Set the entry point
ENV PYTHONFILE=/app/suggestions/src/app.py

CMD ["sh", "-c", "python $PYTHONFILE"]