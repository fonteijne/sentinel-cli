# Updating Agent Prompts

## Overview

When updating agent system prompts, you must ensure validators and parsers remain aligned with the prompt template structure.

## Key Files

| Component | Location | Purpose |
|-----------|----------|---------|
| Prompts | `/workspace/.agents/prompts/{agent_name}.md` | System prompt templates |
| Prompt Loader | `src/prompt_loader.py` | Loads prompts from disk |
| Validators | `src/agents/{agent_name}.py` | Validates LLM output structure |

## Common Pitfall: Prompt-Validator Mismatch

### Symptom
```
Failed to generate valid plan - LLM output missing required sections.
Missing sections: Implementation Steps, Security Considerations
```

### Cause
The prompt template defines one set of section names, but the validator checks for different names.

**Example:**
- Prompt template uses: `## Step-by-Step Tasks`
- Validator checks for: `## Implementation Steps`

### Fix
Update the validator's `required_sections` dict to match the prompt template:

```python
# In src/agents/plan_generator.py
required_sections = {
    "Step-by-Step Tasks": ["step-by-step tasks", "## step-by-step tasks"],
    "Testing Strategy": ["testing strategy", "## testing strategy"],
    # ... must match prompt template exactly
}
```

## Checklist: Updating a Prompt

1. [ ] Edit the prompt file in `.agents/prompts/{agent_name}.md`
2. [ ] Identify any section headers the LLM is expected to generate
3. [ ] Find the validator in `src/agents/{agent_name}.py`
4. [ ] Update `required_sections` to match new section names
5. [ ] Run tests: `poetry run pytest tests/test_{agent_name}.py -v`
6. [ ] Test manually: `sentinel plan {ticket_id}`

## Prompt Loading Path

The `PromptLoader` calculates the prompts directory as:
```python
project_root = Path(__file__).parent.parent.parent  # 3 levels up from src/prompt_loader.py
prompts_dir = project_root / ".agents" / "prompts"
```

This resolves to `/workspace/.agents/prompts/` (workspace root, not sentinel subdirectory).

## Debugging Tips

### Verify prompt is loaded
Check logs for:
```
Loaded system prompt for plan_generator (13472 chars)
```

If chars = 0 or warning about "not found", the prompt file is missing.

### Verify LLM received the prompt
Add debug logging in `base_agent.py`:
```python
logger.debug(f"System prompt preview: {self.system_prompt[:500]}")
```

## Prompt Complexity Guidelines

**Keep prompts simple.** Complex multi-phase prompts can confuse the LLM.

### Signs of an Over-Complicated Prompt
- Multiple "phases" (DETECT → PARSE → EXPLORE → DESIGN → GENERATE)
- The LLM outputs intermediate artifacts (ASCII diagrams, exploration notes) instead of final output
- High tool use count (15+ tools) but wrong final output
- LLM gets "stuck" in an early phase

### Effective Prompt Structure
1. Clear role statement (1-2 sentences)
2. Task description (what to do)
3. Output format (exact structure expected)
4. Available tools (if applicable)
5. Core principles (3-5 bullet points)

### Example: Before and After

**Bad (250 lines, 6 phases):**
```
Phase 0: DETECT - Input Type Resolution
Phase 1: PARSE - Feature Understanding
Phase 2: EXPLORE - Codebase Intelligence
Phase 3: RESEARCH - External Documentation
Phase 4: DESIGN - UX Transformation (ASCII diagrams)
Phase 5: ARCHITECT - Strategic Design
Phase 6: GENERATE - Implementation Plan
```

**Good (90 lines, clear task):**
```
Your Task:
1. Understand requirements
2. Explore codebase with tools
3. Generate structured plan

Output Format: (exact markdown template)
```

---
**Created**: 2026-02-04
**Source**: Debugging session where prompt template v2.0 section names didn't match validator
