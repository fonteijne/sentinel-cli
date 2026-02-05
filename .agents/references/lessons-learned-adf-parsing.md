# Lessons Learned: ADF Parsing Bug

**Date**: 2026-01-24
**Context**: Building `sentinel plan` command
**Bug**: `TypeError: unhashable type: 'slice'` when trying to slice Jira description field

## The Bug

When implementing the `sentinel plan sentest-1` command, we encountered a `TypeError` in the plan generator when trying to truncate the ticket description:

```python
# This failed:
description = ticket_data.get("description", "")
requirements.append(f"Description: {description[:200]}...")
```

**Root cause**: The `description` field from Jira is in Atlassian Document Format (ADF) - a nested dictionary structure, not a plain string.

## Timeline

1. Created ADF parser for `sentinel info` command to display descriptions nicely
2. Implemented `sentinel plan` command
3. Plan generator tried to slice description without parsing it first
4. Got cryptic error: `TypeError: unhashable type: 'slice'`

## Key Lessons

### 1. Data Format Consistency Across Layers

**Problem**: We created the ADF parser for one use case (`info` command) but didn't trace through all code paths that touch Jira ticket data.

**Lesson**: When you discover a data format quirk (like ADF), audit ALL code paths that consume that data, not just the immediate use case.

**Action**: Search for all usages of `ticket_data.get("description")` and similar patterns.

### 2. Parse at the Boundary vs. Parse at Use

**Current approach**: Parse ADF at each usage point (CLI display, plan generator, etc.)
- ❌ Fragile - easy to forget
- ❌ Repetitive code
- ❌ Inconsistent handling

**Better approach**: Parse ADF at the boundary (in `JiraClient.get_ticket()`)
- ✅ Rest of system always gets plain text
- ✅ Single source of truth
- ✅ "Parse, don't validate" principle

**Trade-off**: Sometimes you want raw data for different formatting needs. But for descriptions, plain text is probably always what we want.

### 3. Type Assumptions Are Dangerous

**Problem**: Code assumed `description` was a string because that's semantically sensible, but no validation enforced it.

**Lesson**: Either:
- Use type hints + runtime validation (`isinstance` checks)
- Or centralize parsing to control data shape throughout the system

**Example of defensive coding**:
```python
# Bad - assumes string
description = ticket_data.get("description", "")
text = description[:200]  # Crashes if description is dict

# Good - validates type
description_raw = ticket_data.get("description", "")
if isinstance(description_raw, dict):
    description = parse_adf_to_text(description_raw)
else:
    description = str(description_raw)
```

### 4. Cryptic Error Messages

**Problem**: `TypeError: unhashable type: 'slice'` doesn't immediately say "you're trying to slice a dict".

**Lesson**: Python's error messages for type mismatches can be confusing. Explicit type checks with clear error messages help debugging:

```python
if not isinstance(description, str):
    raise TypeError(
        f"Expected description to be string, got {type(description).__name__}. "
        "Did you forget to parse ADF format?"
    )
```

### 5. Integration Testing Gaps

**Problem**: We tested `sentinel info` manually but not `sentinel plan` until the user ran it.

**Lesson**: End-to-end tests that exercise full workflows would catch these integration issues:
- Fetch ticket → parse → display ✅
- Fetch ticket → parse → analyze → generate plan ❌ (missed this)

**Recommendation**: Add integration tests for each command's full workflow.

## Immediate Fix Applied

Modified `plan_generator.py` to parse ADF before using description:

```python
# Extract from description
description_raw = ticket_data.get("description", "")
if description_raw:
    # Parse ADF format to plain text
    if isinstance(description_raw, dict):
        description = parse_adf_to_text(description_raw)
    else:
        description = str(description_raw)

    # Now safe to slice
    if len(description) > 200:
        requirements.append(f"Description: {description[:200]}...")
```

## Recommended Future Improvement

**Refactor `JiraClient.get_ticket()` to parse ADF at the boundary:**

```python
def get_ticket(self, ticket_id: str) -> Dict[str, Any]:
    """Fetch ticket from Jira with parsed description."""
    ticket_data = self._fetch_from_api(ticket_id)

    # Parse ADF description to plain text at the boundary
    if "description" in ticket_data:
        raw_desc = ticket_data["description"]
        if isinstance(raw_desc, dict):
            ticket_data["description"] = parse_adf_to_text(raw_desc)

    return ticket_data
```

**Benefits**:
- Single parsing point
- Rest of codebase can assume string descriptions
- No risk of forgetting to parse in new commands
- Consistent behavior across all commands

**Considerations**:
- May need to preserve raw ADF for some use cases (add `description_raw` field?)
- Update all existing code that expects ADF format
- Document the contract clearly

## Files Modified

- `src/agents/plan_generator.py` - Added ADF parsing before string operations
- `src/cli.py` - Already had ADF parsing for display
- `src/utils/adf_parser.py` - Comprehensive ADF to plain text parser

## Related References

- Atlassian Document Format (ADF) specification: https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/
- Parse, don't validate: https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/

## Action Items for Future

- [ ] Audit all usages of Jira ticket fields for similar issues
- [ ] Consider moving ADF parsing to `JiraClient` boundary
- [ ] Add integration tests for `plan`, `execute`, `review` commands
- [ ] Document data formats in `JiraClient` docstrings
- [ ] Consider adding runtime type validation with Pydantic models
