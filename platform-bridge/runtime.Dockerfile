FROM python:3.11-slim

WORKDIR /app
COPY requirements-bridge.lock ./requirements-bridge.lock
RUN pip install --no-cache-dir -r requirements-bridge.lock
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
