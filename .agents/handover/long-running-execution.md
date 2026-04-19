# Long-Running Execution (1+ hour)

## Observed

- `sentinel execute` started at **2026-04-18 15:25:28** and was still running at **16:33:42** (68+ minutes)
- Ticket: DHL project (DHLEXS_DHLEXC or COE_JIRATESTAI, exact ticket TBD)
- No crash — just kept running

## Symptoms to investigate

1. **Developer agent looping** — may be retrying failed tasks endlessly without hitting max iterations
2. **Stuck tool call** — Claude SDK subprocess may be hanging on a tool (e.g. `docker compose exec` inside appserver)
3. **No per-task timeout** — `AgentSDKWrapper.execute_with_tools()` has no timeout per tool use or per task
4. **No overall execution timeout** — the `for iteration in range(1, max_iterations + 1)` loop in `cli.py` gates iterations but not wall-clock time

## Where to look

- `sentinel-dev` container logs: `docker logs sentinel-dev --tail 200`
- Agent SDK wrapper logs: look for repeated `[agent_name] Tool use:` lines (looping) or a single `Query sent` with no follow-up (hung)
- Check if an appserver container is still running: `docker ps | grep appserver`

## Likely fixes

1. **Add wall-clock timeout to execute loop** — e.g. `--timeout 1800` (30 min default), check `time.monotonic()` each iteration
2. **Add per-task timeout in BaseDeveloperAgent.run()** — if a single task takes >10 min, skip it and log
3. **Add SDK-level timeout** — `AgentSDKWrapper` should have a configurable timeout for the full query+stream cycle
4. **Better progress logging** — log task start/end timestamps so hangs are immediately visible

## Related code

- `src/cli.py` — execute loop (~line 549)
- `src/agents/base_developer.py` — task loop (~line 659), `run()` method
- `src/agent_sdk_wrapper.py` — `execute_with_tools()` method, stream handling

## Priority

High — a stuck execution blocks the pipeline and wastes API credits with no useful output.
