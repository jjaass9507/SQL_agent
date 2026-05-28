FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Session data directory
RUN mkdir -p data

EXPOSE 5000

# Use Gunicorn in production: 4 workers × 2 threads, 120s timeout for LLM calls
CMD ["gunicorn", \
     "--workers", "4", \
     "--threads", "2", \
     "--timeout", "120", \
     "--bind", "0.0.0.0:5000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
