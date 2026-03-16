FROM python:3.12-slim

WORKDIR /app

# Copy package metadata and source before install; setuptools needs both.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Keep legacy bot as fallback (can use: python bot_legacy.py)
COPY bot_legacy.py .

# Create data directory for SQLite
RUN mkdir -p /app/data
ENV DB_PATH=/app/data/bot_state.db

# Use new modular entry point
CMD ["python", "-m", "twitter_intel.main"]

# Legacy bot available as fallback:
# CMD ["python", "bot_legacy.py"]
