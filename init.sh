#!/bin/bash
#
# Sentinel Initialization Script
# Sets up the environment so you can use 'sentinel' directly instead of 'poetry run sentinel'
#
# Usage:
#   source init.sh       # Initialize and activate environment
#   ./init.sh --install  # Full installation (first time setup)
#

# Determine if we're being sourced or executed
_SENTINEL_SOURCED=0
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    _SENTINEL_SOURCED=1
fi

# Helper function to exit/return appropriately
_sentinel_abort() {
    if [ "$_SENTINEL_SOURCED" -eq 1 ]; then
        return 1 2>/dev/null || true
    else
        exit 1
    fi
}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory (works even when sourced)
_SENTINEL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Save original directory to restore later
_SENTINEL_ORIG_DIR="$(pwd)"

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}                    Sentinel Initialization                   ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Check Prerequisites
# ─────────────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}1️⃣  Checking prerequisites...${NC}"

_SENTINEL_PREREQ_OK=1

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo -e "   ${RED}❌ Python 3 not found${NC}"
    echo "   Please install Python 3.11 or higher"
    _SENTINEL_PREREQ_OK=0
else
    _PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    _PYTHON_MAJOR=$(echo "$_PYTHON_VERSION" | cut -d. -f1)
    _PYTHON_MINOR=$(echo "$_PYTHON_VERSION" | cut -d. -f2)

    if [ "$_PYTHON_MAJOR" -lt 3 ] || ([ "$_PYTHON_MAJOR" -eq 3 ] && [ "$_PYTHON_MINOR" -lt 11 ]); then
        echo -e "   ${RED}❌ Python 3.11+ required (found $_PYTHON_VERSION)${NC}"
        _SENTINEL_PREREQ_OK=0
    else
        echo -e "   ${GREEN}✓${NC} Python $_PYTHON_VERSION"
    fi
fi

# Check Poetry
if ! command -v poetry &> /dev/null; then
    echo -e "   ${RED}❌ Poetry not found${NC}"
    echo ""
    echo "   Install Poetry with:"
    echo "     curl -sSL https://install.python-poetry.org | python3 -"
    echo ""
    echo "   Or via pipx:"
    echo "     pipx install poetry"
    _SENTINEL_PREREQ_OK=0
else
    _POETRY_VERSION=$(poetry --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
    echo -e "   ${GREEN}✓${NC} Poetry $_POETRY_VERSION"
fi

# Check Git
if ! command -v git &> /dev/null; then
    echo -e "   ${RED}❌ Git not found${NC}"
    echo "   Please install Git"
    _SENTINEL_PREREQ_OK=0
else
    _GIT_VERSION=$(git --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
    echo -e "   ${GREEN}✓${NC} Git $_GIT_VERSION"
fi

if [ "$_SENTINEL_PREREQ_OK" -eq 0 ]; then
    echo ""
    echo -e "${RED}Prerequisites check failed. Please install missing dependencies.${NC}"
    cd "$_SENTINEL_ORIG_DIR"
    _sentinel_abort
    return 1 2>/dev/null || exit 1
fi

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Install Dependencies
# ─────────────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}2️⃣  Installing dependencies...${NC}"

cd "$_SENTINEL_SCRIPT_DIR"

# Check if we need to install dependencies
if [ "$1" = "--install" ] || [ ! -d ".venv" ]; then
    echo "   Installing Python dependencies with Poetry..."
    if ! poetry install --no-interaction; then
        echo -e "   ${RED}❌ Failed to install dependencies${NC}"
        cd "$_SENTINEL_ORIG_DIR"
        _sentinel_abort
        return 1 2>/dev/null || exit 1
    fi
    echo -e "   ${GREEN}✓${NC} Dependencies installed"
else
    echo -e "   ${GREEN}✓${NC} Dependencies already installed (use --install to reinstall)"
fi

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Configure Environment
# ─────────────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}3️⃣  Configuring environment...${NC}"

# Check if .env exists
if [ ! -f "config/.env" ]; then
    if [ -f "config/.env.example" ]; then
        echo -e "   ${YELLOW}⚠${NC}  config/.env not found"
        echo ""
        echo "   Creating from template..."
        cp config/.env.example config/.env
        echo -e "   ${GREEN}✓${NC} Created config/.env from template"
        echo ""
        echo -e "   ${YELLOW}⚠ IMPORTANT:${NC} Edit config/.env with your API credentials:"
        echo "     - JIRA_API_TOKEN"
        echo "     - JIRA_EMAIL"
        echo "     - JIRA_BASE_URL"
        echo "     - GITLAB_API_TOKEN"
        echo "     - GITLAB_BASE_URL"
        echo ""
        echo "     LLM Configuration (auto-detected mode):"
        echo "     - API_KEY + BASE_URL: Custom proxy"
        echo "     - API_KEY only: Direct Anthropic API"
        echo "     - Neither: Claude Code subscription"
        echo ""
    else
        echo -e "   ${YELLOW}⚠${NC}  config/.env.example not found - skipping env setup"
    fi
else
    echo -e "   ${GREEN}✓${NC} config/.env exists"
fi

# Load environment variables
if [ -f "config/.env" ]; then
    echo "   Loading environment variables..."
    set -a
    # Use a subshell to avoid issues with malformed .env files
    if source "config/.env" 2>/dev/null; then
        echo -e "   ${GREEN}✓${NC} Environment variables loaded"
    else
        echo -e "   ${YELLOW}⚠${NC}  Could not load config/.env (check for syntax errors)"
    fi
    set +a
fi

# Disable Claude Code auto-updater to prevent orphaned npm processes
# See: https://github.com/anthropics/claude-code/issues/114
export DISABLE_AUTOUPDATER=1
echo -e "   ${GREEN}✓${NC} DISABLE_AUTOUPDATER=1 (prevents npm install conflicts)"

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Activate Virtual Environment
# ─────────────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}4️⃣  Activating virtual environment...${NC}"

# Get the path to the virtual environment
_VENV_PATH=$(poetry env info --path 2>/dev/null || echo "")

if [ -z "$_VENV_PATH" ]; then
    echo -e "   ${RED}❌ Virtual environment not found${NC}"
    echo "   Run: poetry install"
    cd "$_SENTINEL_ORIG_DIR"
    _sentinel_abort
    return 1 2>/dev/null || exit 1
fi

# Check if we're being sourced or executed
if [ "$_SENTINEL_SOURCED" -eq 1 ]; then
    # Script is being sourced - can activate venv
    if [ -f "$_VENV_PATH/bin/activate" ]; then
        source "$_VENV_PATH/bin/activate"
        echo -e "   ${GREEN}✓${NC} Virtual environment activated"
        # Add sentinel to PATH if needed (for editable install)
        export PATH="$_VENV_PATH/bin:$PATH"
    else
        echo -e "   ${RED}❌ Could not find venv activate script${NC}"
        cd "$_SENTINEL_ORIG_DIR"
        return 1
    fi
else
    # Script is being executed - cannot activate venv in parent shell
    echo -e "   ${YELLOW}ℹ${NC}  Script was executed (not sourced)"
    echo ""
    echo "   To activate the environment, run ONE of:"
    echo ""
    echo -e "   ${GREEN}Option 1:${NC} Source this script"
    echo "     source init.sh"
    echo ""
    echo -e "   ${GREEN}Option 2:${NC} Activate manually"
    echo "     source $_VENV_PATH/bin/activate"
    echo ""
    echo -e "   ${GREEN}Option 3:${NC} Use poetry shell"
    echo "     poetry shell"
    echo ""
fi

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Verify Installation
# ─────────────────────────────────────────────────────────────────────────────
echo -e "${YELLOW}5️⃣  Verifying installation...${NC}"

# Check if sentinel command is available
if [ "$_SENTINEL_SOURCED" -eq 1 ]; then
    # Script was sourced, sentinel should be available
    if command -v sentinel &> /dev/null; then
        _SENTINEL_VERSION=$(sentinel --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
        echo -e "   ${GREEN}✓${NC} sentinel command available (v$_SENTINEL_VERSION)"
    else
        echo -e "   ${YELLOW}⚠${NC}  sentinel command not found in PATH"
        echo "   Try running: poetry install"
    fi
else
    # Check via poetry
    if poetry run sentinel --version &> /dev/null; then
        _SENTINEL_VERSION=$(poetry run sentinel --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
        echo -e "   ${GREEN}✓${NC} sentinel installed (v$_SENTINEL_VERSION)"
    else
        echo -e "   ${YELLOW}⚠${NC}  sentinel not installed properly"
        echo "   Try running: poetry install"
    fi
fi

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Done!
# ─────────────────────────────────────────────────────────────────────────────
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}                    ✅ Initialization Complete                ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

if [ "$_SENTINEL_SOURCED" -eq 1 ]; then
    echo -e "${GREEN}You can now use 'sentinel' directly:${NC}"
    echo ""
    echo "  sentinel --help          # Show available commands"
    echo "  sentinel validate        # Validate API credentials"
    echo "  sentinel info PROJ-123   # View Jira ticket info"
    echo "  sentinel plan PROJ-123   # Generate implementation plan"
    echo "  sentinel execute PROJ-123 # Execute the plan"
    echo ""
else
    echo -e "${YELLOW}Next steps:${NC}"
    echo ""
    echo "  1. Source this script to activate the environment:"
    echo "     ${GREEN}source init.sh${NC}"
    echo ""
    echo "  2. Then use sentinel directly:"
    echo "     sentinel --help"
    echo "     sentinel validate"
    echo ""
fi

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Restore original directory if we changed it
cd "$_SENTINEL_ORIG_DIR"

# Clean up temporary variables
unset _SENTINEL_SOURCED _SENTINEL_SCRIPT_DIR _SENTINEL_ORIG_DIR _SENTINEL_PREREQ_OK
unset _PYTHON_VERSION _PYTHON_MAJOR _PYTHON_MINOR _POETRY_VERSION _GIT_VERSION
unset _VENV_PATH _SENTINEL_VERSION
unset -f _sentinel_abort
