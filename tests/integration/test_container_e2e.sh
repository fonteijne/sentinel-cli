#!/usr/bin/env bash
# End-to-end test for container orchestration.
# Run this on a machine with Docker available (developer MacBook or inside Sentinel container with socket mount).
#
# Usage:
#   bash tests/integration/test_container_e2e.sh
#
# Prerequisites:
#   - Docker daemon running
#   - docker compose available
#   - Python 3.11+ with sentinel deps installed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }
info() { echo -e "${YELLOW}→ $1${NC}"; }

# -------------------------------------------------------------------
# Pre-flight checks
# -------------------------------------------------------------------
info "Checking Docker..."
docker version > /dev/null 2>&1 || fail "Docker not available. Is the daemon running?"
docker compose version > /dev/null 2>&1 || fail "Docker Compose not available."
pass "Docker + Compose available"

# -------------------------------------------------------------------
# Setup: create a temporary project with .lando.yml
# -------------------------------------------------------------------
TEST_DIR=$(mktemp -d)
TICKET_ID="TEST-E2E-001"
trap 'info "Cleaning up..."; python3 -c "
from src.compose_runner import ComposeRunner
try:
    r = ComposeRunner(project_name=\"sentinel-${TICKET_ID}\".lower())
    r.down(volumes=True)
except: pass
" 2>/dev/null; rm -rf "$TEST_DIR"' EXIT

info "Creating test project in $TEST_DIR..."

cat > "$TEST_DIR/.lando.yml" <<'LANDO'
name: e2e-test
recipe: drupal10
config:
  webroot: web
  php: "8.2"
  database: mysql
services:
  cache:
    type: redis
LANDO

mkdir -p "$TEST_DIR/web"
echo "<?php phpinfo();" > "$TEST_DIR/web/index.php"
pass "Test project created with .lando.yml"

# -------------------------------------------------------------------
# Test 1: Project type detection
# -------------------------------------------------------------------
info "Test 1: Project type detection..."
DETECTED=$(python3 -c "
from src.environment_manager import EnvironmentManager
from unittest.mock import MagicMock
import src.environment_manager as em
em.get_config = lambda: MagicMock(get=lambda k, d={}: d)
mgr = EnvironmentManager()
print(mgr.detect_project_type(__import__('pathlib').Path('$TEST_DIR')))
")
[ "$DETECTED" = "lando" ] || fail "Expected 'lando', got '$DETECTED'"
pass "Detected project type: lando"

# -------------------------------------------------------------------
# Test 2: Lando translation
# -------------------------------------------------------------------
info "Test 2: Translating .lando.yml..."
python3 -c "
from src.lando_translator import LandoTranslator
t = LandoTranslator.from_file(__import__('pathlib').Path('$TEST_DIR/.lando.yml'))
result = t.translate('$TICKET_ID')
services = list(result['services'].keys())
print(f'Services: {services}')
assert 'appserver' in services, 'Missing appserver'
assert 'database' in services, 'Missing database'
assert 'cache' in services, 'Missing cache'
print('Translation OK')
" || fail "Translation failed"
pass "Lando translation produces appserver, database, cache"

# -------------------------------------------------------------------
# Test 3: Generate compose file
# -------------------------------------------------------------------
info "Test 3: Generating docker-compose.sentinel.yml..."
python3 -c "
from src.lando_translator import LandoTranslator
from pathlib import Path
t = LandoTranslator.from_file(Path('$TEST_DIR/.lando.yml'))
yaml_str = t.translate_to_yaml('$TICKET_ID')
(Path('$TEST_DIR') / 'docker-compose.sentinel.yml').write_text(yaml_str)
print('Compose file written')
"
[ -f "$TEST_DIR/docker-compose.sentinel.yml" ] || fail "Compose file not created"
pass "docker-compose.sentinel.yml generated"

# -------------------------------------------------------------------
# Test 4: Validate compose file
# -------------------------------------------------------------------
info "Test 4: Validating compose file..."

# Create the external volume if it doesn't exist
docker volume create sentinel-projects > /dev/null 2>&1 || true

docker compose -f "$TEST_DIR/docker-compose.sentinel.yml" \
  -p "sentinel-${TICKET_ID}" config > /dev/null 2>&1 \
  || fail "Compose file is invalid"
pass "Compose file validates with docker compose config"

# -------------------------------------------------------------------
# Test 5: Start containers
# -------------------------------------------------------------------
info "Test 5: Starting containers..."
python3 -c "
from src.compose_runner import ComposeRunner
from pathlib import Path
runner = ComposeRunner(
    compose_file=Path('$TEST_DIR/docker-compose.sentinel.yml'),
    project_name='sentinel-$TICKET_ID'.lower(),
)
result = runner.up()
if not result.success:
    print(f'STDERR: {result.stderr}')
    raise RuntimeError('Failed to start')
print('Containers started')
" || fail "Container startup failed"
pass "Containers started"

# -------------------------------------------------------------------
# Test 6: Wait for healthy
# -------------------------------------------------------------------
info "Test 6: Waiting for services to be healthy (up to 60s)..."
python3 -c "
from src.compose_runner import ComposeRunner
from pathlib import Path
runner = ComposeRunner(
    compose_file=Path('$TEST_DIR/docker-compose.sentinel.yml'),
    project_name='sentinel-$TICKET_ID'.lower(),
)
healthy = runner.wait_for_healthy(timeout=60, poll_interval=3)
if not healthy:
    services = runner.ps()
    for s in services:
        print(f'  {s.name}: state={s.state} health={s.health}')
    raise RuntimeError('Services not healthy')
print('All services healthy')
" || fail "Health check timed out"
pass "All services healthy"

# -------------------------------------------------------------------
# Test 7: List running services
# -------------------------------------------------------------------
info "Test 7: Listing running services..."
python3 -c "
from src.compose_runner import ComposeRunner
from pathlib import Path
runner = ComposeRunner(
    compose_file=Path('$TEST_DIR/docker-compose.sentinel.yml'),
    project_name='sentinel-$TICKET_ID'.lower(),
)
services = runner.ps()
print(f'Running services: {len(services)}')
for s in services:
    print(f'  {s.name}: {s.state} ({s.health or \"no healthcheck\"})')
running = [s for s in services if s.state == 'running']
assert len(running) >= 3, f'Expected >= 3 running services, got {len(running)}'
" || fail "Service listing failed"
pass "3+ services running"

# -------------------------------------------------------------------
# Test 8: Execute command in appserver
# -------------------------------------------------------------------
info "Test 8: Executing 'php -v' in appserver..."
PHP_VERSION=$(python3 -c "
from src.compose_runner import ComposeRunner
from pathlib import Path
runner = ComposeRunner(
    compose_file=Path('$TEST_DIR/docker-compose.sentinel.yml'),
    project_name='sentinel-$TICKET_ID'.lower(),
)
result = runner.exec('appserver', 'php -v')
print(result.stdout.split(chr(10))[0])
") || fail "Exec failed"
echo "  $PHP_VERSION"
[[ "$PHP_VERSION" == *"PHP 8.2"* ]] || fail "Expected PHP 8.2, got: $PHP_VERSION"
pass "PHP 8.2 confirmed in appserver"

# -------------------------------------------------------------------
# Test 9: Execute command in database
# -------------------------------------------------------------------
info "Test 9: Checking MySQL connectivity..."
python3 -c "
from src.compose_runner import ComposeRunner
from pathlib import Path
runner = ComposeRunner(
    compose_file=Path('$TEST_DIR/docker-compose.sentinel.yml'),
    project_name='sentinel-$TICKET_ID'.lower(),
)
result = runner.exec('database', 'mysql -u drupal -pdrupal -e \"SELECT 1\"')
assert result.success, f'MySQL query failed: {result.stderr}'
print('MySQL query OK')
" || fail "MySQL connectivity test failed"
pass "MySQL accessible from database container"

# -------------------------------------------------------------------
# Test 10: Teardown
# -------------------------------------------------------------------
info "Test 10: Tearing down containers..."
python3 -c "
from src.compose_runner import ComposeRunner
from pathlib import Path
runner = ComposeRunner(
    compose_file=Path('$TEST_DIR/docker-compose.sentinel.yml'),
    project_name='sentinel-$TICKET_ID'.lower(),
)
result = runner.down(volumes=True)
assert result.success, f'Teardown failed: {result.stderr}'
print('Teardown complete')
" || fail "Teardown failed"

# Verify containers are gone
REMAINING=$(docker ps --filter "label=com.docker.compose.project=sentinel-${TICKET_ID}" -q 2>/dev/null | wc -l)
[ "$REMAINING" -eq 0 ] || fail "Found $REMAINING orphan containers after teardown"
pass "All containers removed"

# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------
echo ""
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "${GREEN}  All 10 tests passed!${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"
