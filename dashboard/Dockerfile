# ══════════════════════════════════════════════════════════════════
# Stage 1: Build the React frontend
# ══════════════════════════════════════════════════════════════════
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Install dependencies first for better caching
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --legacy-peer-deps

# Copy source and build
COPY frontend/ ./
RUN npm run build


# ══════════════════════════════════════════════════════════════════
# Stage 2: Python backend + pre-built frontend static files
# ══════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./backend/

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Expose port
EXPOSE 8000

# Config directory (will be mounted from host)
VOLUME ["/app/config"]

# Working directory is the sentinel-cli root (for CLI subprocess calls)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
