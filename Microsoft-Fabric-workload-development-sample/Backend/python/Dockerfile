# Use Microsoft-approved Python image based on CBL-Mariner
FROM mcr.microsoft.com/cbl-mariner/base/python:3 AS builder

WORKDIR /app

# Install system dependencies if needed
RUN tdnf update -y && \
    tdnf install -y gcc build-essential && \
    tdnf clean all

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Final production image
FROM mcr.microsoft.com/cbl-mariner/base/python:3 AS production

WORKDIR /app

# Install only production dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY src/ ./src/

# Install shadow-utils for useradd command, then create non-root user
RUN tdnf install -y shadow-utils && \
    tdnf clean all && \
    useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Set environment variables
ENV PYTHON_ENVIRONMENT=Production
ENV PYTHONPATH=/app

# Health check 
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health').read()" || exit 1

# Expose port
EXPOSE 5000

# Run the application 
CMD ["python3", "src/main.py"]