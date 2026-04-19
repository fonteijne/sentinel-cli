# Decision: Pipeline-Driven Validation (Post-Pilot)

## Status: Deferred — revisit after DHL pilot succeeds

## Context

Sentinel validates developer agent output with PHPUnit tests and (as of the pilot) a hardcoded `drush site:install minimal --config-dir=../config/sync` step in `DrupalDeveloperAgent`. This matches DHL's actual GitLab CI `test:test-site` stage exactly.

## Problem

Each project has its own `.gitlab-ci.yml` with different validation steps. Hardcoding per-stack validation commands won't scale beyond the pilot.

## Proposed direction: Parse project pipeline definitions

Instead of hardcoding validation commands per stack, Sentinel should read the project's CI pipeline file (`.gitlab-ci.yml`, `.github/workflows/*.yml`, etc.) and extract the test/validation stages to replicate.

### DHL's pipeline as reference

The DHL `.gitlab-ci.yml` `test:test-site` stage runs:

```yaml
script:
  - cd web
  - ../vendor/bin/drush --verbose site:install minimal --config-dir=../config/sync -y --db-url="${DB_DRIVER}://${MYSQL_USER}:${MYSQL_PASSWORD}@${DB_HOST}/${MYSQL_DATABASE}" --account-name=root --account-pass=rootpass --account-mail=root@iodigital.com
  - bash ../scripts/deploy.sh --clean
```

Key observations:
- Uses `site:install` with `--config-dir`, not `config:import --dry-run`
- Requires MySQL service, composer artifacts from build stage
- Config path is `config/sync` (project-specific)
- Post-install `deploy.sh --clean` also runs

### What a general solution needs to handle

- **YAML parsing** with anchors, includes, variable expansion
- **Service dependencies** (MySQL, Redis, etc.) — map to container setup
- **Artifact dependencies** between stages (build outputs feeding test inputs)
- **Image-specific tooling** (e.g., `composer2` vs `composer`, `drush` path)
- **Variable interpolation** from CI variables
- **Multiple pipeline providers** (GitLab CI, GitHub Actions, Bitbucket Pipelines)

### Suggested approach

1. Start with GitLab CI only (most customers use it)
2. Extract `test` stage scripts from `.gitlab-ci.yml`
3. Map `services:` blocks to container dependencies
4. Interpolate variables with sensible defaults
5. Run extracted commands inside the appserver container

### Why we deferred this

- Pipeline parsing is complex (YAML edge cases, variable expansion, includes)
- DHL is the only pilot target — hardcoded validation matches their CI exactly
- Building a parser before proving the validation loop works is premature
- Risk of over-engineering before we know what other projects' pipelines look like

## Trigger to revisit

When onboarding the second project, compare its `.gitlab-ci.yml` to DHL's. If the test stages differ meaningfully, this work becomes necessary.
