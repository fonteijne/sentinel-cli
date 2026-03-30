# Drupal Planning Overlay

This overlay activates when the target project is a Drupal codebase. It supplements the base plan generator prompt with Drupal-specific knowledge.

## Drupal File Structure Conventions

```
web/                          # Drupal webroot
  core/                       # Drupal core (NEVER modify)
  modules/
    contrib/                  # Third-party modules (NEVER modify directly)
    custom/                   # Our custom modules (this is where we work)
  themes/
    contrib/                  # Third-party themes
    custom/                   # Our custom themes
  sites/default/
    settings.php              # Main settings (DB, config sync dir, etc.)
    settings.local.php        # Local overrides (gitignored)
    services.yml              # Service container overrides
config/sync/                  # Exported configuration YAML (managed by drush cex/cim)
drush/                        # Drush config and site aliases
composer.json                 # Dependencies (modules, patches, PHP version)
```

## Drupal Module Anatomy

Every custom module lives in `web/modules/custom/{module_name}/` and contains:

| File | Purpose |
|------|---------|
| `{module}.info.yml` | Module metadata, dependencies, core compatibility |
| `{module}.module` | Procedural hook implementations |
| `{module}.install` | Install/uninstall hooks and update hooks (`hook_update_N()`) |
| `{module}.routing.yml` | Route definitions (URL paths to controllers) |
| `{module}.services.yml` | Dependency injection service definitions |
| `{module}.permissions.yml` | Permission definitions |
| `{module}.libraries.yml` | CSS/JS asset libraries |
| `{module}.links.menu.yml` | Menu link definitions |
| `{module}.links.task.yml` | Tab link definitions |
| `src/Controller/` | Route controllers |
| `src/Form/` | Form classes (FormBase, ConfigFormBase, ConfirmFormBase) |
| `src/Plugin/` | Plugin classes (Block, Field, Views, QueueWorker, Action) |
| `src/Entity/` | Custom entity types |
| `src/EventSubscriber/` | Event subscriber classes |
| `src/Service/` | Service classes (registered in *.services.yml) |
| `config/install/` | Default config installed with the module |
| `config/schema/` | Config schema definitions |
| `templates/` | Twig templates |
| `tests/src/Unit/` | PHPUnit unit tests |
| `tests/src/Kernel/` | Kernel tests (with limited bootstrap) |
| `tests/src/Functional/` | Full browser tests |

## Key Drupal Concepts for Planning

### Dependency Injection
- Services are defined in `*.services.yml` and injected via constructors
- NEVER use `\Drupal::service()` in classes - always use constructor injection
- Controllers, forms, and plugins use `create()` factory method for DI
- Service tags: `event_subscriber`, `cache.bin`, `breadcrumb_builder`, etc.

### Hook System
- Procedural hooks in `.module` files: `function {module}_{hookname}()`
- Drupal 11+ supports attribute-based hooks: `#[Hook('form_alter')]`
- Common hooks: `hook_form_alter`, `hook_preprocess_*`, `hook_entity_*`, `hook_theme`
- Hook ordering depends on module weight in system table

### Plugin System
- Annotated classes in `src/Plugin/` directories
- Block plugins: `@Block` annotation, extend `BlockBase`
- Field types: `@FieldType`, `@FieldWidget`, `@FieldFormatter`
- Views plugins: `@ViewsFilter`, `@ViewsField`, `@ViewsSort`
- Queue workers: `@QueueWorker` annotation

### Configuration Management
- **Config** (exportable): settings, field storage, views, content types
- **State** (not exportable): last cron run, maintenance mode, per-environment values
- Config changes require `drush cex` (export) and `drush cim` (import)
- Schema changes to config entities require update hooks in `.install` files

### Render Pipeline
- NEVER print output directly - always use render arrays
- Render arrays use `#type`, `#markup`, `#theme` keys
- Cacheable render arrays include `#cache` with tags, contexts, max-age
- Cache tags enable targeted invalidation (e.g., `node:123`, `node_list`)

### Entity API
- Content entities: nodes, users, taxonomy terms, custom entities
- Config entities: views, image styles, content types
- Entity storage, query, and access patterns

## Plan Template Additions for Drupal

When generating Step-by-Step Tasks, ALWAYS include:

### Required Drush Commands
- `drush cr` - Clear cache (after routing, service, or plugin changes)
- `drush cex` - Export config (after config entity changes)
- `drush cim` - Import config (on deployment)
- `drush updb` - Run update hooks (after adding update hooks)
- `drush en {module}` - Enable new modules

### Update Hooks
If the plan involves:
- Changing stored config schema: add `hook_update_N()` in `.install`
- Migrating data: add `hook_update_N()` with batch processing
- New module dependency: add `hook_update_N()` to enable it

### Validation Commands for Drupal
```
Level 1: STATIC_ANALYSIS
  - phpstan analyse (if configured)
  - phpcs --standard=Drupal,DrupalPractice

Level 2: UNIT_TESTS
  - phpunit --testsuite=unit

Level 3: KERNEL_TESTS
  - phpunit --testsuite=kernel

Level 4: FULL_SUITE
  - phpunit (all suites)
  - drush cr && drush cex --diff (verify config is clean)
```

## Common Drupal Gotchas

| Gotcha | Impact | Mitigation |
|--------|--------|------------|
| Forgetting `drush cr` after changes | New routes/services/plugins not recognized | Add `drush cr` after every structural change in plan |
| Using `\Drupal::service()` in classes | Untestable code, violates DI principle | Always use constructor injection via `create()` |
| Missing cache tags on render arrays | Stale content displayed to users | Include `#cache` metadata with appropriate tags |
| Config not exported after changes | Config lost on next deployment | Always `drush cex` and commit config changes |
| Missing permissions for new routes | 403 errors for authorized users | Define permissions in `*.permissions.yml` |
| Modifying contrib modules directly | Patches lost on `composer update` | Use composer patches (`cweagans/composer-patches`) |
| Missing config schema | Config import fails on other environments | Always create `config/schema/*.schema.yml` |
| Forgetting update hooks for data changes | Data inconsistency across environments | Add `hook_update_N()` for any stored data changes |
| Not checking module weight for hook order | Hooks fire in wrong order | Document expected hook execution order |
