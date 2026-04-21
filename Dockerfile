# Sentinel Docker Container
# Includes: Python 3.11, Poetry, Beads (bd), and Claude Code
#
# Multi-stage build for optimal caching:
# - base: System deps, Node.js, Poetry, Claude Code, Beads (slow, rarely changes)
# - app: Application dependencies and code (faster, changes often)

# =============================================================================
# BASE STAGE - Heavy installations (cached unless base image changes)
# =============================================================================
FROM python:3.11-slim-bookworm AS base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

# Add Poetry to PATH
ENV PATH="$POETRY_HOME/bin:$PATH"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    openssh-client \
    ca-certificates \
    gnupg \
    zsh \
    && rm -rf /var/lib/apt/lists/*

# SSH config: accept host keys automatically for git clone over SSH
RUN mkdir -p /root/.ssh \
    && printf "Host *\n    StrictHostKeyChecking accept-new\n    UserKnownHostsFile /root/.ssh/known_hosts\n" \
       > /root/.ssh/config \
    && chmod 700 /root/.ssh \
    && chmod 600 /root/.ssh/config

# Install Docker CLI + Compose plugin (DooD — no daemon, talks to host via socket)
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (required for Claude Code)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -

# Install Dolt (database backend required by Beads)
RUN curl -L https://github.com/dolthub/dolt/releases/latest/download/install.sh | bash

# Install Claude Code and Beads globally via npm (slow - ~13 min)
RUN npm install -g @anthropic-ai/claude-code @beads/bd

# Git identity for commits made inside the container
RUN git config --global user.email "sentinel.utrecht@iodigital.com" \
    && git config --global user.name "Sentinel"

# Create Claude Code directory structure (prevents CLI issues in containers)
RUN mkdir -p /etc/claude-code/.claude/skills \
    && mkdir -p /root/.claude \
    && echo '{}' > /root/.claude.json

# =============================================================================
# APP STAGE - Application code (rebuilds faster on code changes)
# =============================================================================
FROM base AS app

# Set working directory
WORKDIR /app

# Copy dependency files first (for better layer caching)
COPY pyproject.toml poetry.lock ./

# Install Python dependencies (without dev deps for production)
RUN poetry install --no-root --no-dev

# Copy the rest of the application
COPY . .

# Install the project itself
RUN poetry install --only-root

# Create workspace directory for Sentinel operations
RUN mkdir -p /workspaces

# Create empty .env.local for auth configure (ensures bind mount works)
RUN touch /app/config/.env.local

# Set default environment variables
ENV WORKSPACE_ROOT=/workspaces

# Default command - run sentinel CLI
ENTRYPOINT ["sentinel"]
CMD ["--help"]

# =============================================================================
# DEV STAGE - Includes dev dependencies
# =============================================================================
FROM base AS dev

WORKDIR /app

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install ALL dependencies including dev
RUN poetry install --no-root

# Copy application (in dev, this is usually mounted instead)
COPY . .

# Install the project
RUN poetry install --only-root

RUN mkdir -p /workspaces

# Create empty .env.local for auth configure (ensures bind mount works)
RUN touch /app/config/.env.local

ENV WORKSPACE_ROOT=/workspaces

# No entrypoint for dev - allows flexible command execution
CMD ["bash"]
