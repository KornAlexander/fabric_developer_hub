# Microsoft Fabric Python Backend - Deployment Guide

## Environment Configuration

### Development

```bash
# No environment variables needed, uses appsettings.json defaults
python src/main.py
```

### Production

```bash
# Required environment variables
export PYTHON_ENVIRONMENT=Production
export PUBLISHER_TENANT_ID=your-tenant-id
export CLIENT_ID=your-client-id
export CLIENT_SECRET=your-client-secret
export AUDIENCE=your-audience

# Optional SSL for HTTPS
export SSL_KEYFILE=/path/to/private.key
export SSL_CERTFILE=/path/to/certificate.crt

# Run the application
python src/main.py
```

## Docker Deployment

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/

# Set production environment
ENV PYTHON_ENVIRONMENT=Production

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

# Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "src/main.py"]
```

## Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fabric-python-backend
spec:
  replicas: 3
  selector:
    matchLabels:
      app: fabric-python-backend
  template:
    metadata:
      labels:
        app: fabric-python-backend
    spec:
      containers:
      - name: app
        image: your-registry/fabric-python-backend:latest
        ports:
        - containerPort: 5000
        env:
        - name: PYTHON_ENVIRONMENT
          value: "Production"
        - name: PUBLISHER_TENANT_ID
          valueFrom:
            secretKeyRef:
              name: fabric-secrets
              key: publisher-tenant-id
        - name: CLIENT_ID
          valueFrom:
            secretKeyRef:
              name: fabric-secrets
              key: client-id
        - name: CLIENT_SECRET
          valueFrom:
            secretKeyRef:
              name: fabric-secrets
              key: client-secret
        livenessProbe:
          httpGet:
            path: /health
            port: 5000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 5000
          initialDelaySeconds: 5
          periodSeconds: 5
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
```

## Monitoring

- Health endpoint: `GET /health`
- Readiness endpoint: `GET /ready`
- Metrics: Check logs for request processing times
- Logs: Located in platform-specific directories
  - Windows: `%APPDATA%\Microsoft_Fabric_Python_Backend\logs`
  - macOS: `~/Library/Application Support/Microsoft_Fabric_Python_Backend/logs`
  - Linux: `~/.config/Microsoft_Fabric_Python_Backend/logs`
  