# Drupal Developer Overlay

This overlay activates when implementing features in a Drupal codebase. It supplements the base developer prompt with Drupal-specific implementation knowledge.

## Drupal File Structure

```
web/
  core/                       # NEVER modify
  modules/
    contrib/                  # NEVER modify directly
    custom/                   # Our custom modules — this is where we work
  themes/
    custom/                   # Our custom themes
  sites/default/
    settings.php
config/sync/                  # Exported config YAML (drush cex/cim)
composer.json                 # Dependencies
```

## Module Anatomy

Every custom module in `web/modules/custom/{module}/`:

| File | Purpose |
|------|---------|
| `{module}.info.yml` | Module metadata and dependencies |
| `{module}.module` | Procedural hook implementations |
| `{module}.install` | Install/uninstall and update hooks |
| `{module}.routing.yml` | Route definitions |
| `{module}.services.yml` | DI service definitions |
| `{module}.permissions.yml` | Permission definitions |
| `{module}.libraries.yml` | CSS/JS asset libraries |
| `src/Controller/` | Route controllers |
| `src/Form/` | Form classes |
| `src/Plugin/` | Plugin classes (Block, Field, Views) |
| `src/Entity/` | Custom entity types |
| `src/EventSubscriber/` | Event subscribers |
| `src/Service/` | Service classes |
| `config/install/` | Default config installed with module |
| `config/schema/` | Config schema definitions |
| `templates/` | Twig templates |

## TDD Cycle for Drupal

### RED — Write Failing Test
- Create test in `tests/src/Unit/{TestName}Test.php`
- Extend `\Drupal\Tests\UnitTestCase`
- Use `@covers` annotation pointing to the class under test
- Run: `vendor/bin/phpunit --filter={TestName}Test`
- Test MUST fail

### GREEN — Minimal Implementation
- Implement in the correct module directory
- Follow Drupal coding standards
- Run: `vendor/bin/phpunit --filter={TestName}Test`
- Test MUST pass

### REFACTOR
- Apply DRY, extract services if needed
- Run `drush cr` after structural changes (routes, services, plugins)
- Run: `vendor/bin/phpunit --filter={TestName}Test`
- Tests MUST still pass

## Test Structure

```
tests/
  src/
    Unit/           # Fast, no Drupal bootstrap. Mock dependencies.
    Kernel/         # Partial bootstrap, real database.
    Functional/     # Full browser tests.
    FunctionalJavascript/  # JS-dependent tests.
```

**Unit test template:**
```php
<?php

namespace Drupal\Tests\{module}\Unit;

use Drupal\Tests\UnitTestCase;

/**
 * @coversDefaultClass \Drupal\{module}\{ClassName}
 * @group {module}
 */
class {ClassName}Test extends UnitTestCase {

  /**
   * @covers ::methodName
   */
  public function testMethodName(): void {
    // Arrange, Act, Assert
  }

}
```

## Validation Commands

```
Level 1: STATIC ANALYSIS
  phpcs --standard=Drupal,DrupalPractice web/modules/custom/{module}/
  phpstan analyse web/modules/custom/{module}/ (if configured)

Level 2: UNIT TESTS
  vendor/bin/phpunit --testsuite=unit --filter={module}

Level 3: FULL SUITE
  vendor/bin/phpunit
  drush cr
```

## Critical Rules

1. **NEVER modify core or contrib** — use patches via `composer.json` if needed
2. **Dependency Injection** — always use constructor injection via `create()` factory. NEVER use `\Drupal::service()` in classes
3. **Render arrays** — NEVER use `echo` or `print`. Return render arrays with `#type`, `#markup`, `#theme`
4. **Cache metadata** — include `#cache` with tags, contexts, max-age on all render arrays
5. **drush cr** — run after any change to routes, services, plugins, or hooks
6. **Config export** — run `drush cex` after config entity changes and commit the YAML
7. **Update hooks** — add `hook_update_N()` in `.install` for schema or data changes

## Hook Implementation

**Procedural (all Drupal versions):**
```php
function {module}_form_alter(&$form, FormStateInterface $form_state, $form_id) {
  // ...
}
```

**Attribute-based (Drupal 11+):**
```php
#[Hook('form_alter')]
public function formAlter(&$form, FormStateInterface $form_state, $form_id): void {
  // ...
}
```

## Common Patterns

### Service definition (`{module}.services.yml`):
```yaml
services:
  {module}.my_service:
    class: Drupal\{module}\Service\MyService
    arguments: ['@entity_type.manager', '@logger.factory']
```

### Route definition (`{module}.routing.yml`):
```yaml
{module}.page:
  path: '/my-path'
  defaults:
    _controller: '\Drupal\{module}\Controller\MyController::page'
    _title: 'Page Title'
  requirements:
    _permission: '{module} access'
```

### Plugin (Block example):
```php
/**
 * @Block(
 *   id = "{module}_example",
 *   admin_label = @Translation("Example Block"),
 * )
 */
class ExampleBlock extends BlockBase implements ContainerFactoryPluginInterface {
  // Use create() for DI
}
```
