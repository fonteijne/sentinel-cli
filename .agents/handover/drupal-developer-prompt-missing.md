# Missing `drupal_developer.md` System Prompt

## Status: Open — low priority, cosmetic

## Observed

During `sentinel execute DHLEXS_DHLEXC-383 --revise` (2026-04-19), the log shows:

```
WARNING - System prompt not found for drupal_developer: Prompt file not found: /app/prompts/drupal_developer.md
```

This warning is **benign** — `BaseDeveloperAgent.__init__` catches the missing prompt and loads the fallback `prompts/developer.md`, then `DrupalDeveloperAgent._load_stack_overlay()` appends the Drupal-specific overlay from `prompts/overlays/drupal_developer.md`. The agent works correctly.

## Why it happens

`BaseAgent.__init__` tries to load `prompts/{agent_name}.md` for every agent. When `agent_name="drupal_developer"`, it looks for `prompts/drupal_developer.md` which doesn't exist. The fallback chain:

1. `BaseAgent.__init__` → tries `prompts/drupal_developer.md` → not found → WARNING logged, `system_prompt = ""`
2. `BaseDeveloperAgent.__init__` → sees empty prompt → loads `prompts/developer.md` as fallback
3. `DrupalDeveloperAgent.__init__` → appends `prompts/overlays/drupal_developer.md`

Same applies to `python_developer`.

## Options to fix

### Option A: Create `prompts/drupal_developer.md` as a symlink or re-export
Create `prompts/drupal_developer.md` that just includes `developer.md`. Eliminates the warning without changing code.

### Option B: Suppress the warning in `BaseAgent` for developer agents
In `BaseAgent.__init__`, log at DEBUG instead of WARNING when the prompt is not found, since subclasses may handle the fallback themselves.

### Option C: Pass a flag to skip initial prompt loading
Add a `skip_prompt_load` parameter to `BaseAgent.__init__` that `BaseDeveloperAgent` passes as `True`, then handles prompt loading itself.

## Related files

- `src/agents/base_agent.py:53-58` — WARNING log on missing prompt
- `src/agents/base_developer.py:39-47` — fallback prompt loading
- `src/agents/drupal_developer.py:46-56` — overlay loading
- `prompts/developer.md` — shared developer prompt
- `prompts/overlays/drupal_developer.md` — Drupal-specific overlay
