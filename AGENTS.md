## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles map to the default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `docs/GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Development

One-command checks: `node scripts/check.mjs`; details in `docs/public/development.md`.
Agent kernel: `src/noteagent/agent/`; harness map: `docs/agent-harness.md`; writing tools:
`src/noteagent/writing/tools.py`; the assembled tool registry:
`src/noteagent/api/session_access.py`. `docs/tool-contract.md` is the historical
propose-era contract.
