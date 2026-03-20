FROM python:3.12-slim

WORKDIR /app

# Copy package metadata and source before install; setuptools needs both.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Create data directory for SQLite
RUN mkdir -p /app/data
ENV DB_PATH=/app/data/bot_state.db

# Use the modular entry point
CMD ["python", "-m", "twitter_intel.main"]
