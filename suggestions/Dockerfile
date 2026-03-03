# Use an official Python runtime as the base image
FROM python:3.11

WORKDIR /app

# Install dependencies
COPY ./suggestions/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Start the suggestions gRPC service via hotreload helper (mounted code will be used in dev)
CMD python utils/other/hotreload.py "suggestions/src/app.py"
