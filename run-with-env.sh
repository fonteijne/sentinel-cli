#!/bin/bash
# Helper script to run Sentinel with environment variables loaded

set -e

# Check if .env exists
if [ ! -f "config/.env" ]; then
    echo "❌ Error: config/.env not found"
    echo ""
    echo "Create it first:"
    echo "  cp config/.env.example config/.env"
    echo "  # Edit config/.env with your credentials"
    exit 1
fi

# Load environment variables
echo "📦 Loading environment variables from config/.env"
export $(grep -v '^#' config/.env | xargs)

# Run the command
echo "▶️  Running: poetry run sentinel $@"
echo ""
poetry run sentinel "$@"
