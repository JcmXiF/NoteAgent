# Recovery and rollback evaluation

A local, quantitative experiment over the two halves of the recovery work: chat
run recovery and effect-journal rollback. It is not a CI gate. It needs a real
provider connection, starts a real server process, and kills that process with a
real SIGKILL, so it refuses to run under CI unless `--force` is passed.

## Running it

```bash
python -m evals.recovery --list
python -m evals.recovery
python -m evals.recovery --suite recovery --reps 1
python -m evals.recovery --filter R2
```

- `--suite all|recovery|rollback` defaults to `all`.
- `--reps` defaults to `3`; every scenario repeats three times.
- `--filter` keeps scenarios whose id contains the text.
- `--report` defaults to `build/evals/recovery/report.json`; the Markdown
  summary is written beside it.
- `--artifacts` defaults to `build/evals/recovery/artifacts/`; every attempt
  keeps its temp project, server log and (on an exception) a traceback.
- `--tool-delay-ms` defaults to `300`: each wiki tool handler waits that long
  before running, which widens the window the kill can aim at.
- `--force` is required when `CI` or `GITHUB_ACTIONS` is set.

The model connection is the one saved in the app's settings or in
`NOTEAGENT_LLM_*`; the CLI calls `install_view()` and `load_llm_config()` and
exits `2` when no connection is configured.

## Scenarios

Each scenario is an end-to-end experiment: seed a project, drive the real
HTTP/SSE API, kill or disconnect at a known boundary, restart, resume, and
measure.

| Id | Suite | What it does |
|---|---|---|
| R1 | recovery | Closes the SSE stream mid-answer; the run pauses, then resumes. |
| R2 | recovery | SIGKILLs between a committed tool call and its result. |
| R3 | recovery | SIGKILLs between the user message and the start marker. |
| R4 | recovery | Completes normally, restarts, and must not be reported interrupted. |
| R5 | recovery | Refuses a resume after the model changes; resumes with a stale warning after the wiki changes. |
| R6 | recovery | A loopback proxy fails the first two provider requests; the run retries. |
| B1 | rollback | A person edits a page a run wrote; default restore skips it, force restores it; a later edit is `changed_by_restore`. |
| B2 | rollback | A later run rewrites the page; restoring the first run conflicts. |
| B3 | rollback | An untouched page restores, writes one undo effect, and a repeat restore writes none. |
| B4 | rollback | External, missing and renamed pages are classified and handled. |
| B5 | rollback | A real pipeline compile writes pages; its effect ids drive preview/restore. |
| B6 | rollback | Two real processes append to `effects.jsonl`; no line is lost. |

A run that never reaches its target boundary is `missed`, retried up to three
times, and reported as an infrastructure failure after that. Missed and infra
runs never enter a product denominator.

## Metrics

| Metric | Definition | Target |
|---|---|---|
| Interrupt detection | interrupted runs the startup scan marked / interrupted runs | 100% |
| False interrupts | normally completed runs reported interrupted / normal runs | 0% |
| Content preservation | entries committed before resume that still exist / committed entries, over runs that attempted a resume | 100% |
| Tool replay | duplicate tool results for the same call id (the chat toolset is read-only, so this is the observable replay signal) | 0 |
| Resume success | resumes that finish within 12 model calls / resume attempts | >= 90% |
| Recovery time | process start to the interrupted run being visible, p50/p95 | observational |
| Classification accuracy | correct restore classifications / classified pages (all seven classes are sampled) | 100% |
| User-edit overwrite | `changed_by_person` pages a default restore overwrote / those pages | 0% |
| Restore idempotency | repeat restores that wrote no extra undo / repeat restores | 100% |
| Restore success | restores that succeeded / restores that should have | >= 90% |
| Budget completion | chat runs that reached a terminal state within 12 model calls / runs with an observable budget; pipeline model calls are not in the transcript and are excluded | >= 90% |

Safety metrics are read as hard invariants (100% or 0). Completion metrics keep
their exact numerator and denominator in the report; the 90% line is a target,
not a pass/fail gate. The Markdown report carries the metric table, a
per-scenario passed/failed/missed/infra table, the missed attempts and the
infra failures.

## How it is built

- `runner.py` owns the scenario orchestration and the missed-boundary retry
  rule; it never injects a model. The model is the real configured one.
- `server.py` composes the real app with a delayed tool registry and runs it
  under uvicorn in a child process. Production modules are unchanged; the delay
  lives here and is passed through `AppDeps.tools`, the app's existing seam.
- `process.py` chooses a free port, starts the child, waits for its socket and
  kills it. A kill is `SIGKILL` on POSIX and `TerminateProcess` on Windows.
- `boundary.py` infers where a transcript stopped (question only, tool call
  open, tool result open, final answer, settled) the way the startup scan does.
- `client.py` drives the real HTTP/SSE routes; its stream context manager is how
  R1 disconnects mid-answer.
- `proxy.py` is R6's fault injector: a loopback reverse proxy that returns 500
  for the first two requests and then streams the provider through.
- `journal_child.py` is B6's second writer: a tiny process that appends effects.

## Limitations

- Local only, and expensive: a full run makes 36 real-model runs plus restarts.
- Real-model variance is real. The safety metrics are deterministic, but resume
  and pipeline completion can fail for reasons outside the recovery path.
- R3 targets a very narrow window (user entry committed, start marker not). If
  the kernel writes the marker first, the attempt is `missed` and retried; a
  persistent miss is reported as an infra failure, not a product failure.
- B5 uses the real pipeline planner, so it is the slowest and most variable
  scenario; its timeout is 240 seconds per stage wait.
- Replay is measured as duplicate `toolResult` rows. The chat toolset is
  read-only, so there is no side-effect execution to count directly.
- Budget completion covers chat runs, whose model calls are visible in the
  transcript. A pipeline run's model calls are not, so B5 is excluded from that
  denominator rather than counted as a pass.
