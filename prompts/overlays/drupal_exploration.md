# Drupal Exploration Template

When the target codebase is Drupal, replace the generic Phase 2 (EXPLORE) with these specific steps. Execute them in order, documenting findings with `file:line` references.

## Step 1: Module Map

**Goal**: Understand all custom modules and their relationships.

```
Glob: web/modules/custom/*/*.info.yml
```

For each module, document:
- Machine name, human name, package
- Dependencies (other modules it requires)
- Whether it's a base module or feature module

## Step 2: Service Architecture

**Goal**: Map the dependency injection landscape.

```
Glob: web/modules/custom/*/*.services.yml
```

For each service, document:
- Service ID, implementing class
- Constructor arguments (what it depends on)
- Tags (event_subscriber, etc.)

## Step 3: Routing Map

**Goal**: Understand all custom URL paths and their controllers.

```
Glob: web/modules/custom/*/*.routing.yml
```

For each route, document:
- Route name, URL path
- Controller class and method
- Access requirements (permissions, roles)

## Step 4: Hook Inventory

**Goal**: Find all hook implementations that might interact with our changes.

```
Grep: ^function\s+\w+_(form_alter|preprocess|insert|update|delete|access|views_data|theme|cron|mail|tokens)
  in: web/modules/custom/*/*.module

Grep: #\[Hook\(
  in: web/modules/custom/*/src/**/*.php
```

Document each hook's purpose (read the docblock/comments).

## Step 5: Plugin Discovery

**Goal**: Catalog plugin types used in the project.

```
Glob: web/modules/custom/*/src/Plugin/**/*.php
```

Group by plugin type:
- Block plugins (`src/Plugin/Block/`)
- Field plugins (`src/Plugin/Field/`)
- Views plugins (`src/Plugin/views/`)
- QueueWorker plugins (`src/Plugin/QueueWorker/`)
- Other plugin types

## Step 6: Entity & Form Audit

**Goal**: Understand custom entities and forms.

```
Glob: web/modules/custom/*/src/Entity/*.php
Glob: web/modules/custom/*/src/Form/*.php
```

For entities: note type (content vs config), fields, access control.
For forms: note base class (FormBase, ConfigFormBase, ConfirmFormBase), what they do.

## Step 7: Configuration Schema

**Goal**: Understand what config the project manages.

```
Glob: web/modules/custom/*/config/schema/*.schema.yml
Glob: web/modules/custom/*/config/install/*.yml
```

Note what settings are stored, their types, and default values.

## Step 8: Theme Layer

**Goal**: Understand the theming structure and template overrides.

```
Glob: web/themes/custom/*/templates/**/*.html.twig
Glob: web/themes/custom/*/*.theme
```

Note: preprocess functions, template overrides, custom regions.

## Step 9: Testing Infrastructure

**Goal**: Understand how the project tests code.

```
Check: phpunit.xml or phpunit.xml.dist
Glob: web/modules/custom/*/tests/**/*Test.php
```

Note:
- Which test types are used (Unit, Kernel, Functional, FunctionalJavascript)
- Test base classes and traits used
- How test data is set up (fixtures, factories, etc.)

## Step 10: Build & CI Pipeline

**Goal**: Understand the build and deployment toolchain.

```
Check: .gitlab-ci.yml, .github/workflows/, Makefile
Check: package.json (root and in themes)
Check: composer.json scripts section
```

Note:
- Frontend build pipeline (webpack, vite, gulp, npm scripts)
- CI/CD stages and what they validate
- Deployment method (config import, drush commands, etc.)

## Step 11: Composer Patches & Contrib

**Goal**: Know what's been patched and what contrib modules are in play.

```
Read: composer.json → extra.patches section
Read: composer.json → require section (drupal/* packages)
```

Document any patched modules — changes to those modules require extra care.

## Documentation Format

After completing all steps, include a summary table in the plan:

```markdown
## Codebase Overview

| Aspect | Count | Key Items |
|--------|-------|-----------|
| Custom modules | N | module1, module2, ... |
| Custom themes | N | theme1, ... |
| Services | N | service1, service2, ... |
| Routes | N | /path1, /path2, ... |
| Hooks | N | form_alter, preprocess_*, ... |
| Plugins | N | Block: X, Field: Y, Views: Z |
| Test files | N | Unit: X, Kernel: Y, Functional: Z |
```
