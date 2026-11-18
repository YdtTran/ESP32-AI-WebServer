# Base image reusing installed local image for instant startup
FROM iot_assignment-tools:latest

WORKDIR /app

# Copy application files
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8009

# Default command runs stdio transport for direct MCP client attachment
ENTRYPOINT ["python", "-m", "src.server"]
CMD ["--transport", "stdio"]
