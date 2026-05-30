# ClaimsCopilot

[![CI](https://github.com/zhihantan/claimscopilot/actions/workflows/ci.yml/badge.svg)](https://github.com/zhihantan/claimscopilot/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![Platform: Databricks Apps](https://img.shields.io/badge/platform-Databricks_Apps-FF3621.svg)](https://docs.databricks.com/en/dev-tools/databricks-apps/index.html)


Assistive copilot for insurance claims adjusters at **the Company** (a fictional
insurer), built on the Databricks Mosaic AI Agent Framework, Foundation Model
APIs, Vector Search, and Unity Catalog. It's a FastAPI service on Databricks
Apps serving a React SPA; LLM calls go to Foundation Model APIs (optionally
fronted by AI Gateway) and every turn is traced in MLflow.

> **Scope of this release (v0.3.3).** EMEA pilot. Languages: EN, ES, JA.
> Countries: GB, MX, JP. Persona: claims adjusters L1–L3 + Team Lead.
> **Assistive only** — the adjuster always makes the final decision.

---

## What this demonstrates

A production-shaped **agentic app on Databricks**, end to end:

- **Mosaic AI Agent Framework + LangGraph** supervisor (plan → tools → reflect → synthesize), streamed to the browser over SSE.
- **Foundation Model APIs** with a model fallback chain; **Unity Catalog functions**, **Vector Search**, and an optional **Genie space** (NL→SQL analytics) as governed agent tools.
- **MLflow Tracing** on every turn + an **LLM-judge eval** release gate.
- **Databricks Apps** hosting (FastAPI + React SPA), on-behalf-of identity, Secrets, and a **DLT** PII-anonymization pipeline.
- **Two deployment paths from one agent**: it runs in-process in the Databricks App, *and* can be registered to Unity Catalog + served via Model Serving (`scripts/register_agent.py`) and scored with Mosaic AI **Agent Evaluation** (`eval/run_agent_eval.py`).
- Trust controls: server-verified citations, a deterministic vulnerability gate, and confirmation-gated write tools.

Installs into your own workspace in minutes — see [Quickstart](#quickstart--install-in-your-own-workspace).

---

## Architecture

```
React SPA ──► FastAPI (Databricks Apps) ──► LangGraph supervisor
                                                │
                                                ├─► Tool layer
                                                │     ├─ UC Functions (SQL, OBO)
                                                │     ├─ Vector Search (OBO)
                                                │     └─ Python tools (translate, draft)
                                                │
                                                └─► AI Gateway ──► FMAPI
                                                       ▲                │
                                                       └── MLflow Tracing
```

See `Phase 3 — Architecture & Design` in the project doc for the full diagram,
agent state machine, tool list, retrieval design, eval plan, safety controls,
cost model, and rollout plan.

---

## Repository layout

```
backend/        FastAPI app + LangGraph agent + tools + MLflow ChatAgent wrapper
frontend/       React + Vite + Tailwind SPA (prebuilt into backend/static/)
scripts/        Install + ops tooling (see Scripts below)
setup/          UC DDL (01_ddl.sql) + templated SP grants (02_grants.sql)
data/           Synthetic data generator
dlt/            DLT PII-anonymization pipeline (+ jp_pii helpers)
eval/           Golden dataset + MLflow eval + Mosaic AI Agent Evaluation
resources/      Asset-bundle resource defs + the Lakeview dashboard
app.yaml        Databricks Apps runtime config
databricks.yml  Asset Bundle (App, jobs, DLT pipeline)
```

## Scripts

| Script | What it does |
|--------|--------------|
| `init.py` | Configure the clone for your workspace (writes `app.yaml` + the `dev` target) |
| `preflight.py` | Read-only readiness check (endpoints, warehouse, catalog/schemas, VS, MLflow) |
| `bootstrap.py` | One-command provisioning (DDL, secret scope, MLflow exp, VS, seed) — idempotent |
| `grant_app_sp.py` | Grant the App's service principal access to UC objects (stage/prod) |
| `setup_ai_gateway.py` | Add AI Gateway guardrails / rate-limits / usage logging to a serving endpoint |
| `build_dashboard.py` | Build / deploy the Lakeview "Agent Operations" dashboard |
| `register_agent.py` | Log + register + serve the agent (Mosaic AI Agent Framework) |
| `teardown.py` | Remove everything `bootstrap.py` created (dry-run by default) |
| `run_sql_file.py` | Execute a multi-statement SQL file against a warehouse |

---

## Quickstart — install in your own workspace

Prereqs: Databricks CLI ≥ 0.285, an authenticated profile, Python 3.11+. Nothing
below is hardcoded to a specific workspace — `scripts/init.py` writes your values
into `app.yaml` and the `dev` target of `databricks.yml`.

```bash
# 1. Point the project at YOUR workspace (drop --write to preview first).
#    The dev target is host-less — it deploys wherever your CLI profile points.
python scripts/init.py \
  --catalog <your_catalog> --warehouse-id <warehouse-id> --region EMEA --write

# 2. Read-only readiness check — what does the workspace still need?
python scripts/preflight.py --profile <profile> --warehouse-id <warehouse-id> --catalog <your_catalog>

# 3. Provision everything (idempotent): schemas/tables/functions, secret scope,
#    MLflow experiment, Vector Search endpoint + indexes, synthetic data.
python scripts/bootstrap.py --profile <profile> --warehouse-id <warehouse-id> \
  --catalog <your_catalog> --vs-endpoint claimscopilot_vs --skip-claim-narratives

# 4. Deploy the bundle, then push the app code.
#    (bundle deploy syncs files but does NOT restart the App — apps deploy does.)
databricks bundle deploy -t dev -p <profile>
databricks apps deploy claimscopilot \
  --source-code-path /Workspace/Users/<you>/.bundle/claimscopilot/dev/files -p <profile>

# 5. Run the anonymization pipeline once, then build the precedent index it feeds.
databricks bundle run narrative_anonymization -t dev -p <profile>
python scripts/bootstrap.py --profile <profile> --warehouse-id <warehouse-id> \
  --catalog <your_catalog> --vs-endpoint claimscopilot_vs \
  --skip-ddl --skip-secret --skip-mlflow --skip-seed

# 6. stage/prod only (App runs as a service principal): grant the SP access.
python scripts/grant_app_sp.py --profile <profile> --warehouse-id <warehouse-id> \
  --catalog <your_catalog> --app-name claimscopilot --apply
```

Notes:
- `bootstrap.py` is idempotent — `--dry-run` previews; `--seed-mode if-empty` (default) won't double-seed.
- In `dev` mode the App runs as you (the deploying user), so step 6 is only for `stage`/`prod`.
- AI Gateway is optional — the app calls FMAPI directly out of the box. To add guardrails / rate-limits / usage logging, run `scripts/setup_ai_gateway.py` on a serving endpoint you control, set `CC_GATEWAY_CHAT` to it, and `CC_USE_GATEWAY=true`; the app then calls the guarded endpoint first, with the raw FMAPI endpoints as fallback.
- Genie is optional — set `CC_GENIE_SPACE_ID` (in `app.yaml`) to a Genie space over your claims tables to enable the NL→SQL `query_genie_space` tool. Unset, it's simply not offered to the agent.

---

## What the bundle deploys

`databricks bundle deploy -t <target>` creates:
- the **claimscopilot** App (FastAPI + React serving the agent),
- the **seed_data**, **eval_full**, and **eval_quick** jobs,
- the **narrative_anonymization** DLT pipeline.

Targets: `dev` (the maintainer's workspace — customers re-point it with
`scripts/init.py`), plus host-less `stage` / `prod` that deploy to whichever
workspace your CLI profile is authenticated to. Catalog + warehouse come from
the per-target `variables` (or `--var`). After deploy, the App URL is in the
Apps UI; run `databricks bundle run eval_full -t <target>` for the release gate.

---

## Local development

You need Python 3.11+, Node 20+, the Databricks CLI configured, and a
personal access token for a workspace user that has the same grants as the
prod App service principal (or a subset, for safety).

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install

# Two terminals:

# 1) FastAPI
cd backend
export DATABRICKS_HOST=https://<workspace>.cloud.databricks.com
export CC_DEV_OBO_TOKEN=$(databricks tokens create --comment claimscopilot-dev --lifetime-seconds 3600 | jq -r .token_value)
export CC_WAREHOUSE_ID=<warehouse-id>
export CC_SYSTEM_CANARY=$(openssl rand -hex 8)
export CC_APP_ENV=dev
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

# 2) Vite dev server (proxies /api → 8000)
cd frontend
npm run dev
# open http://localhost:5173
```

Open `http://localhost:5173?claim=<a-real-claim_id>` to anchor the chat to a
specific claim. With no `?claim=` parameter the app runs in "ask anything"
mode.

---

## How the agent works (1-pager)

1. **Plan.** LangGraph PLAN node calls Claude 3.7 Sonnet with the tool registry
   + a minimal claim summary. Returns an ordered tool plan as JSON.
2. **Execute.** TOOL EXECUTOR runs UC Function / Vector Search / Python tools
   in order, with per-tool timeouts, retries (idempotent reads only),
   and a per-turn budget of 8 tool calls.
3. **Reflect.** REFLECT node decides if there's enough evidence; can add up to
   2 more tools. Max 3 reflect cycles per turn.
4. **Synthesize.** SYNTHESIZE node streams the final answer with citations,
   a `<decision>` tag, and a structured next step.
5. **Log.** Writes to `<catalog>.app.message`, `app.tool_call`, and (when the
   adjuster confirms) `app.decision_log`. Every span emits to MLflow Tracing.

Safety:
- AI Gateway PII + jailbreak + content guardrails on every LLM call.
- Server-side citation verifier strips unverified `[POLICY §… / wording v…]`.
- Vulnerability classifier auto-escalates on bereavement/hardship/regulator
  signals before any synthesis.
- Write tools (`log_decision_rationale`, `escalate_to_human`) require an
  explicit confirmation token from the UI.

---

## Common workflows

**Ask anything mode.** Open without `?claim=` and ask general policy questions.
The agent will only call tools it can answer without a specific claim id.

**Coverage inquiry.** "Is the cracked screen covered? What's the excess?" —
agent will call `get_claim`, `get_policy_terms`, `compute_excess`, and
`search_policy_wordings`, then synthesize with citation `[POLICY §3.2 / wording v2025-04]`.

**Customer message drafting.** "Draft an empathetic approval message in
Spanish." — agent calls `draft_customer_comm`; the draft appears in the
assistant bubble and is **not** sent. The adjuster copies it into the comms
system.

**Precedent search.** "Find precedents for partial settlements on liquid
damage." — agent calls `search_similar_claims` and returns anonymized
precedents with `[CLAIM-xxxx similar]` citations.

**Approve & Log.** Click **Approve & Log** in the message footer to confirm
the adjuster concurs; the UI re-sends the turn with a confirmation token,
which unlocks `log_decision_rationale` to write to `decision_log`.

---

## Eval & release gate

Every PR runs `eval/run_eval.py --mode quick`. Pre-deploy runs
`--mode full`. The release gate enforces:

| Gate | Threshold |
|------|-----------|
| citation precision (mean) | ≥ 0.95 |
| refusal correctness (mean) | = 1.0 |
| escalation correctness (mean) | = 1.0 |
| tool-set F1 (mean) | ≥ 0.85 |

Failing the gate fails the job and blocks promotion.

---

## Operations

- **Kill switch (fast):** set `CC_ENABLED=false` in the App env and redeploy.
  `/api/chat` returns 503 with a localized message; the rest of the workbench
  is unaffected.
- **Version rollback:** `databricks bundle deploy --target prod` from a prior
  Git ref. Decision-log rows are immutable; reversed recommendations are
  written as new rows with `superseded_by`.
- **Ops dashboard:** `python scripts/build_dashboard.py --catalog <cat> --warehouse-id <id> --apply`
  creates a Lakeview "Agent Operations" dashboard (decision volume + mix, adjuster
  concurrence, feedback, escalations by reason) over the app's audit tables. With
  AI Gateway enabled, its usage/inference table adds per-request cost + latency.
- **Drift alert:** `eval_full` runs every Monday and posts to
  `#claimscopilot-live` on Slack via the bundle's notifications block (add
  your `webhook_notifications` block to the job for your environment).

---

## Durable agent state (Lakebase checkpointer)

The agent graph checkpoints its per-turn state through a LangGraph checkpointer,
selected by `CC_CHECKPOINTER`:

| Mode | Saver | Survives container restart? |
|------|-------|------------------------------|
| `memory` (default) | in-process `MemorySaver` | No |
| `none` | — | No |
| `lakebase` | `AsyncPostgresSaver` on Lakebase Postgres | Yes |

State flows through the checkpointer per super-step. A completed turn is purged
on finish; a turn interrupted by a crash stays in the store and is resumable.
The OBO token is redacted from state before it is checkpointed, so it is never
persisted.

**Enable `lakebase`:**
1. `scripts/create_lakebase.sh` — provisions the Lakebase project + database.
2. Attach the project to the App as a **database resource** (the runtime then
   injects `PGHOST`/`PGUSER`/`PGPORT`/`PGDATABASE`).
3. In `app.yaml` set `ENDPOINT_NAME` and flip `CC_CHECKPOINTER=lakebase`.
4. Bump `databricks-sdk` (needs the `w.postgres` Lakebase API — see
   `requirements.txt`) and redeploy. The app runs `AsyncPostgresSaver.setup()`
   on boot to create the checkpoint tables (the App SP needs `CREATE` on the DB).

> Full cross-restart *resume* of an in-flight turn additionally needs the client
> to re-send the turn id; today the durable saver guarantees state isn't lost
> and is inspectable/resumable server-side.

---

## License

Licensed under the [Apache License 2.0](LICENSE). "The Company" is a fictional
composite used for the demo scenario — this repo contains no real customer data
(all data is synthetic, generated by `data/seed_synthetic.py`).
