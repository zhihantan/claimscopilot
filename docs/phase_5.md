# Phase 5 — Demo & Storytelling

> Pilot scope: ClaimsCopilot v0.3.3 — EMEA, GB/MX/JP, en/es/ja, adjusters L1–L3 + Team Lead. Assistive only; the adjuster signs off on every decision.

---

## 1. Five-minute demo script

Open the App URL with a seeded claim: `https://<app>/?claim=CLM-GB-1042`.

### 0:00 — Land on the workbench (15s)

> *Show.* Sidebar with sample sessions, header with adjuster identity + region, footer with trace id and latency badge, claim summary chip pinned to the chat input.

> *Say.* "This is what an adjuster sees first thing each morning — yesterday's open work on the left, the claim they're working now anchored in the chat. They've already seen the customer's narrative; the copilot is a research assistant, not a robot adjuster."

### 0:15 — Coverage inquiry, EN (45s)

> *Type.* `Is the cracked screen covered? What's the excess?`

> *Watch.* In order:
> 1. **PLAN card** appears: `get_claim`, `get_policy_terms`, `compute_excess`, `search_policy_wordings`.
> 2. Four **tool cards** stream in with latency badges; each one shows the args and a 200-char result preview.
> 3. **REFLECT** card: `done`.
> 4. **Streamed synthesis**: prose answer with `[POLICY §3.2 / wording v2025-04]` rendered as a citation chip; a `<decision class="APPROVE" confidence="HIGH" />` tag is rendered as a green pill in the message footer.

> *Say.* "Notice three things: every tool call is visible — there is no hidden agent reasoning. Every citation is server-verified against the actual policy wording the tool returned — the synthesizer can't hallucinate citations and have them appear in the UI. And the `<decision>` tag is just a recommendation — the adjuster decides next."

### 1:00 — Approve & Log (30s)

> *Click.* The **Approve & Log** button in the message footer.

> *Watch.* A confirmation token is sent on the next turn; the agent re-emits the turn, and this time the `log_decision_rationale` write-tool fires (visible in the tool trace). A toast confirms the decision row was inserted.

> *Say.* "Write tools — anything that mutates state — only unlock when the UI sends an explicit confirmation. The agent cannot self-confirm. That's how we keep humans in the loop on a system the regulator will want to audit."

### 1:30 — Customer-comm drafting in Spanish (60s)

> *Type.* `Draft an empathetic approval message in Spanish for the customer.`

> *Watch.* The agent calls `draft_customer_comm` (tone=`empathetic`, language=`es`). The draft streams into the assistant bubble. It is **not** sent — there's a copy-to-clipboard button and a note that the adjuster pastes it into the comms system.

> *Say.* "The agent never talks to customers directly. Every customer-visible message is drafted, reviewed, and dispatched by a human through the existing comms platform. The agent's job is to make the human five minutes faster, not to replace them."

### 2:30 — Precedent search (60s)

> *Type.* `Find precedents for partial settlements on liquid damage.`

> *Watch.* `search_similar_claims` returns four `[CLAIM-7f3a2b91 similar]` style chips. Hover reveals decision, paid band, product, and country. None contain a real claim id or any PII — they're pulled from the DLT-anonymized `narrative_anon` table.

> *Say.* "These are real prior decisions, anonymized through a Delta Live Tables pipeline that strips PII via `ai_mask`. Adjusters historically had no good way to surface 'what did we do last time on something like this' — the precedent search is the highest-rated feature in the L1 cohort's week-1 feedback."

### 3:30 — Vulnerability auto-escalation (45s)

> *Type.* `Honestly, my husband just passed and I can't afford to pay the excess right now.`

> *Watch.* No PLAN card. No tool calls. The agent emits a single localized message (`REFUSAL_VULNERABILITY`) and shows an escalation chip linking to the `VULN_CARE` queue.

> *Say.* "Bereavement signals, hardship signals, regulator signals — none of these get policy reasoning. The vulnerability classifier fires *before* the planner runs, the adjuster gets a routed handoff, and the customer gets a kind, human response. This is the kind of behavior compliance asked us to ship on day one."

### 4:15 — Eval gate, one slide (45s)

> *Show.* The MLflow eval run from `eval_full` showing the four gates.

> *Say.* "Every change runs against a 20-example golden set on PR; the same eval blocks promotion if citation precision dips below 0.95 or refusal/escalation correctness drops below 1.0. That's how we keep this from regressing as we tune the prompt."

---

## 2. Three "wow" moments

1. **Reasoning is visible, not magic.** PLAN → TOOL × N → REFLECT → SYNTHESIZE renders as discrete UI blocks before the prose arrives. Stakeholders who've been burned by "trust me" LLM demos can audit the trail in real time.

2. **Server-verified citations.** A `[POLICY §3.2 / wording v2025-04]` chip can only render if the synthesizer's claim matches a `ref` that one of this turn's retrieval tools actually returned. Hallucinated citations are dropped before the bytes reach the browser.

3. **Vulnerability gating beats the planner.** The cheapest, safest LLM response to a bereavement or hardship signal is *no LLM response at all*. A deterministic 200µs regex pre-empts the planner and hands the customer to a human queue — a feature compliance can demo to the FCA without flinching.

---

## 3. Known issues & status (v0.3.3 hardening)

The pilot's three known runtime issues have been addressed. What changed, and
what honestly still remains:

1. **Streaming now runs *on* the compiled LangGraph (was: graph built but unused).**
   `run_stream` previously hand-rolled a plan→execute→reflect loop that diverged
   from the compiled graph (it re-planned every cycle; the graph plans once). It
   now drives the compiled graph via `astream(stream_mode="updates")`, so there
   is a single execution path and state flows through a checkpointer (per-turn
   `thread_id`, purged on completion). This also fixed a real UI bug:
   `tool.start`/`tool.end` now share a `call_id`, so tool cards resolve out of
   the spinner — previously the ids never matched. A latent re-execution bug in
   `node_execute` (it re-ran the head of the plan on multi-cycle reflect) was
   fixed to run only the un-executed tail. A **durable Lakebase (Postgres)
   checkpointer** is now implemented (`CC_CHECKPOINTER=lakebase` →
   `AsyncPostgresSaver` on a token-refreshing psycopg pool; the OBO token is
   redacted from state so it is never persisted). The default stays `memory`
   (in-process; lost on container restart). *Remaining:* enabling `lakebase`
   needs deploy-time wiring that can't be validated from the build env — a live
   Lakebase project (`scripts/create_lakebase.sh`), the database resource
   attached to the App, and a `databricks-sdk` bump for the `w.postgres` API.
   Full cross-restart *resume* of an in-flight turn also needs the client to
   re-send the turn id.

2. **Blocking SDK calls no longer wedge the event loop.** Every
   `statement_execution.execute_statement` in the async tools (`get_claim` et
   al.) and the FastAPI session/feedback routes now runs via
   `asyncio.to_thread`, so a stalled SQL warehouse keeps the loop responsive and
   concurrent tool calls overlap instead of serializing (covered by
   `tests/test_async_no_block.py`). *Remaining:* a wedged thread still cannot be
   hard-cancelled — the SDK `wait_timeout` (≤30s) plus its `CANCEL`
   on-wait-timeout bound how long an orphaned worker thread lingers.

3. **`ai_mask` is now backed by a Japanese postal/address regex pass.** The DLT
   pipeline applies `dlt/jp_pii.py` after `ai_mask`: a language-agnostic 〒
   postal mask, plus (JP rows only) bare postal codes (`150-0001`) and
   street-number fragments (`1丁目2番3号`) that NER misses
   (`tests/test_jp_pii.py`). *Remaining:* the regex covers postal/number forms,
   not every free-text address — a Week-1 sampling audit of JP narratives before
   the precedent index goes live to adjusters is still recommended.

---

## 4. One-slide exec summary

> **ClaimsCopilot — Pilot in flight, EMEA.** Assistive agent for claims adjusters; reads from the lakehouse, drafts comms, surfaces precedents, escalates the vulnerable; the adjuster decides.

| Metric | Target | Owner |
|---|---|---|
| Time-to-first-useful-answer (p95) | ≤ 2.5s | Eng |
| Citation precision (LLM-judge, n=20 golden) | ≥ 0.95 | Eng + Legal |
| Adjuster concurrence with recommendation | ≥ 80% | Ops |
| Cost per turn (FMAPI + retrieval) | < $0.04 | Eng |
| Refusal correctness on legal / auto-decision asks | 1.00 | Legal |
| Vulnerability auto-escalation correctness | 1.00 | Compliance |

**Why now / why us.**

- Mosaic AI Agent Framework, FMAPIs, Unity Catalog, Vector Search, AI Gateway — every component is GA on Databricks; nothing self-hosted.
- Adjuster-facing only; no customer-direct generation. Every write needs a UI-issued confirmation token.
- Kill-switch is a single env var (`CC_ENABLED=false` → 503 in <1s).
- Cost & drift watched weekly via Lakeview dashboards + the `eval_full` job that auto-runs Mondays.

**Pilot rollout.**

- Week 0: shadow mode, 0 % traffic, MLflow traces only.
- Week 1: 5 % canary on GB-EN adjusters; daily KPI review.
- Week 2: ramp to 25 % if eval gates hold; add ES + JA cohorts.
- Week 4: full pilot scope (L1–L3 + Team Lead), MX & JP regions on.

**Open asks.**

- IAM source-of-truth confirmation for the `user_directory` table (Okta vs Entra).
- Final wording on the bereavement refusal copy from Compliance (ES, JA still draft).
- DLT pipeline runtime budget — currently single-region, single-warehouse.
