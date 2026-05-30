# Contributing to ClaimsCopilot

Thanks for your interest! ClaimsCopilot is a **reference template** for building
agentic apps on Databricks — contributions that improve the framework, fix bugs,
or make it easier to adapt are very welcome.

## Dev setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# frontend (only if you change the UI — a prebuilt bundle ships in backend/static/)
cd frontend && npm ci && npm run build && cd ..
```

## Running the tests

The offline suite needs no Databricks workspace (it mocks the SDK + LLM):

```bash
pytest -q          # the full suite must stay green; CI runs this on every PR
```

The MLflow LLM-judge evals (`eval/run_eval.py`, `eval/run_agent_eval.py`) and the
provisioning scripts DO need a workspace — see the README Quickstart.

## Local dev loop

Two terminals (backend + Vite). See **Local development** in the README. The
backend reads its config from `CC_*` env vars (`backend/agent/config.py`); a
`.devloop.env` stub lets the app boot without a real workspace.

## Project structure — framework vs. domain

The repo separates the **reusable agent framework** from the **claims domain** so
you can adapt it:

| Reusable framework | Claims-specific (swap for your domain) |
|--------------------|----------------------------------------|
| `backend/main.py` (FastAPI, SSE, health/readiness) | `backend/agent/prompts.py` (system prompts, few-shots) |
| `backend/agent/agent.py` (LangGraph loop, streaming, checkpointer) | tool *implementations* + input models in `backend/agent/tools.py` |
| `backend/agent/tools.py` infra (`_tool_span`, error envelopes, registry) | `setup/01_ddl.sql` (tables + UC functions) |
| `backend/readiness.py`, `scripts/*` | `data/seed_synthetic.py`, `eval/golden_dataset.jsonl` |
| `frontend/` shell | the demo narrative ("the Company") |

## Adding an agent tool

1. Add a Pydantic input model (extend `_IdsBase`) in `backend/agent/tools.py`.
2. Write an `async def your_tool(args, user) -> dict` — wrap work in `_tool_span`,
   return the standard error envelope on failure (`_error(...)`), and offload any
   blocking SDK call with `asyncio.to_thread`.
3. Register it in `READ_TOOLS` (or `WRITE_TOOLS` if it mutates state — those are
   gated behind a UI confirmation token) with a clear docstring; the planner sees
   that doc.
4. Add a unit test (mock the SDK, like `tests/test_genie_tool.py`).

## Adapting to your own use case

Keep the framework; replace the domain pieces above. Re-point the catalog with
`scripts/init.py`, regenerate data from your own `seed_synthetic.py`, swap the UC
functions in `setup/01_ddl.sql`, and rewrite the prompts + golden set. The
agent loop, streaming, eval gate, and install tooling carry over unchanged.

## Pull requests

- Keep the **offline test suite green** (`pytest -q`) — CI enforces it.
- Match the surrounding style; add tests for new behavior.
- Don't commit secrets or workspace-specific values (`scripts/init.py` keeps
  those out of the committed source).

## License

By contributing you agree your contributions are licensed under the
[Apache License 2.0](LICENSE).
