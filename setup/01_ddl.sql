-- =============================================================================
-- ClaimsCopilot — Workspace objects (catalogs, schemas, tables, functions)
-- =============================================================================
--
-- Run this as a SQL notebook on a serverless SQL warehouse, as a workspace
-- admin (or a principal with CREATE CATALOG / CREATE SCHEMA in the metastore).
--
-- Idempotent: every statement uses IF NOT EXISTS / OR REPLACE.
--
-- This file creates objects only. SP grants live in `setup/02_grants.sql` —
-- run that AFTER this file, and skip it entirely for `dev` deploys (the App
-- runs as the deploying user in development mode).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Schemas
-- (The catalog is assumed to already exist; CREATE CATALOG requires
-- metastore-level privilege that FEVM users don't have by default.)
-- -----------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS __CATALOG__.policy;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.claims;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.devices;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.repairs;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.partners;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.kb;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.tools
  COMMENT 'Tool surface area exposed to the agent';

CREATE SCHEMA IF NOT EXISTS __CATALOG__.app;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.eval;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.indexes;
CREATE SCHEMA IF NOT EXISTS __CATALOG__.models;

-- =============================================================================
-- READ tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS __CATALOG__.policy.policy (
  policy_id            STRING NOT NULL,
  policy_number        STRING NOT NULL,
  partner_id           STRING NOT NULL,
  product_code         STRING NOT NULL,
  policyholder_id      STRING NOT NULL,
  device_id            STRING NOT NULL,
  effective_date       DATE,
  expiry_date          DATE,
  term_months          INT,
  premium_amount       DECIMAL(12,2),
  currency_code        STRING,
  excess_amount        DECIMAL(12,2),
  coverage_limit_total DECIMAL(12,2),
  claims_used_count    INT,
  status               STRING,
  issue_country        STRING,
  language_pref        STRING,
  data_residency_region STRING,
  cancellation_date    DATE,
  cancellation_reason_code STRING,
  last_endorsement_id  STRING,
  created_at           TIMESTAMP,
  updated_at           TIMESTAMP,
  source_system        STRING,
  _ingest_ts           TIMESTAMP
) USING DELTA
TBLPROPERTIES (delta.enableChangeDataFeed = true);

CREATE TABLE IF NOT EXISTS __CATALOG__.policy.coverage (
  coverage_id          STRING NOT NULL,
  policy_id            STRING NOT NULL,
  coverage_type        STRING NOT NULL,
  sum_insured          DECIMAL(12,2),
  currency_code        STRING,
  excess               DECIMAL(12,2),
  included_perils      ARRAY<STRING>,
  excluded_perils      ARRAY<STRING>,
  endorsements         ARRAY<STRUCT<endorsement_id:STRING, kind:STRING, text:STRING>>,
  effective_from       DATE,
  effective_to         DATE,
  wording_doc_id       STRING,
  wording_version      STRING,
  clauses_struct       STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.policy.wording_document (
  wording_doc_id  STRING NOT NULL,
  product_code    STRING NOT NULL,
  country         STRING NOT NULL,
  language        STRING NOT NULL,
  version         STRING NOT NULL,
  effective_from  DATE NOT NULL,
  effective_to    DATE,
  source_uri      STRING,
  text_full       STRING,
  hash_sha256     STRING,
  created_at      TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.policy.wording_chunk (
  chunk_id        STRING NOT NULL,
  wording_doc_id  STRING NOT NULL,
  version         STRING NOT NULL,
  section_path    STRING,
  section_title   STRING,
  text            STRING NOT NULL,
  token_count     INT,
  language        STRING NOT NULL,
  product_code    STRING,
  country         STRING,
  prev_chunk_id   STRING,
  next_chunk_id   STRING,
  _ingest_ts      TIMESTAMP
) USING DELTA
TBLPROPERTIES (delta.enableChangeDataFeed = true);

CREATE TABLE IF NOT EXISTS __CATALOG__.claims.claim (
  claim_id                       STRING NOT NULL,
  claim_number                   STRING NOT NULL,
  policy_id                      STRING NOT NULL,
  policyholder_id                STRING NOT NULL,
  device_id                      STRING NOT NULL,
  fnol_channel                   STRING,
  fnol_timestamp                 TIMESTAMP,
  incident_timestamp             TIMESTAMP,
  incident_country               STRING,
  incident_description_raw       STRING,
  incident_description_lang      STRING,
  incident_description_en        STRING,
  claim_type                     STRING,
  triage_class                   STRING,
  status                         STRING,
  assigned_adjuster_id           STRING,
  repair_order_id                STRING,
  paid_amount                    DECIMAL(12,2),
  reserve_amount                 DECIMAL(12,2),
  currency_code                  STRING,
  decision_reason_code           STRING,
  fraud_score                    DOUBLE,
  fraud_score_version            STRING,
  decision_made_at               TIMESTAMP,
  resolution_at                  TIMESTAMP,
  cycle_time_hours               DOUBLE,
  language_pref                  STRING,
  data_residency_region          STRING,
  created_at                     TIMESTAMP,
  updated_at                     TIMESTAMP
) USING DELTA
TBLPROPERTIES (delta.enableChangeDataFeed = true);

CREATE TABLE IF NOT EXISTS __CATALOG__.claims.claim_event (
  event_id          STRING NOT NULL,
  claim_id          STRING NOT NULL,
  event_type        STRING NOT NULL,
  event_subtype     STRING,
  actor_type        STRING,
  actor_id          STRING,
  payload_json      STRING,
  note_text         STRING,
  note_text_lang    STRING,
  attachments       ARRAY<STRUCT<doc_id:STRING, mime:STRING, sha256:STRING>>,
  event_ts          TIMESTAMP,
  source_system     STRING
) USING DELTA;

-- __CATALOG__.claims.narrative_anon is created and owned by the
-- narrative_anonymization DLT pipeline (dlt/anonymize_narratives.py).
-- Do NOT pre-create it here — DLT-managed tables conflict with manually
-- created tables of the same name. After the first pipeline run, grant
-- SELECT (see the deferred-grant section near the bottom of this file).

CREATE TABLE IF NOT EXISTS __CATALOG__.devices.device (
  device_id                STRING NOT NULL,
  imei_sha256              STRING,
  serial_sha256            STRING,
  make                     STRING,
  model                    STRING,
  model_year               INT,
  device_category          STRING,
  oem_id                   STRING,
  purchase_country         STRING,
  purchase_date            DATE,
  purchase_price           DECIMAL(12,2),
  current_replacement_cost DECIMAL(12,2),
  oem_warranty_end_date    DATE,
  condition_grade          STRING,
  device_status            STRING,
  last_diagnostic_ts       TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.repairs.repair_order (
  repair_order_id      STRING NOT NULL,
  claim_id             STRING NOT NULL,
  device_id            STRING NOT NULL,
  vendor_id            STRING NOT NULL,
  vendor_country       STRING,
  repair_type          STRING,
  parts_struct         ARRAY<STRUCT<part_sku:STRING, qty:INT, unit_cost:DECIMAL(12,2), currency:STRING>>,
  labor_hours          DECIMAL(6,2),
  labor_rate           DECIMAL(12,2),
  quoted_amount        DECIMAL(12,2),
  invoiced_amount      DECIMAL(12,2),
  variance_amount      DECIMAL(12,2),
  quote_ts             TIMESTAMP,
  started_ts           TIMESTAMP,
  completed_ts         TIMESTAMP,
  qa_pass              BOOLEAN,
  customer_collected_ts TIMESTAMP,
  notes_text           STRING,
  notes_text_lang      STRING,
  invoice_doc_id       STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.partners.partner_account (
  partner_id           STRING NOT NULL,
  partner_name         STRING NOT NULL,
  partner_type         STRING NOT NULL,
  parent_partner_id    STRING,
  region               STRING,
  country              STRING,
  go_live_date         DATE,
  take_rate_pct        DECIMAL(5,2),
  revenue_share_struct STRING,
  slas_struct          STRING,
  account_manager_id   STRING,
  solutions_engineer_id STRING,
  contract_doc_id      STRING,
  status               STRING,
  last_qbr_date        DATE
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.kb.article (
  article_id           STRING NOT NULL,
  title                STRING,
  body_md              STRING,
  language             STRING,
  category             STRING,
  applies_to_products  ARRAY<STRING>,
  applies_to_countries ARRAY<STRING>,
  version              STRING,
  updated_at           TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.kb.article_chunk (
  chunk_id     STRING NOT NULL,
  article_id   STRING NOT NULL,
  language     STRING,
  section_path STRING,
  text         STRING,
  token_count  INT,
  embedding    ARRAY<FLOAT>,
  _ingest_ts   TIMESTAMP
) USING DELTA
TBLPROPERTIES (delta.enableChangeDataFeed = true);

-- =============================================================================
-- WRITE tables (agent + app)
-- =============================================================================

CREATE TABLE IF NOT EXISTS __CATALOG__.app.session (
  session_id        STRING NOT NULL,
  user_id           STRING NOT NULL,
  user_role         STRING NOT NULL,
  claim_id          STRING,
  language          STRING NOT NULL,
  app_version       STRING NOT NULL,
  agent_version     STRING NOT NULL,
  started_at        TIMESTAMP NOT NULL,
  started_date      DATE GENERATED ALWAYS AS (DATE(started_at)),
  last_activity_at  TIMESTAMP NOT NULL,
  closed_at         TIMESTAMP,
  status            STRING NOT NULL,
  metadata          MAP<STRING,STRING>
) USING DELTA
PARTITIONED BY (started_date);

CREATE TABLE IF NOT EXISTS __CATALOG__.app.message (
  message_id        STRING NOT NULL,
  session_id        STRING NOT NULL,
  turn_index        INT NOT NULL,
  role              STRING NOT NULL,
  content           STRING NOT NULL,
  language          STRING,
  citations         ARRAY<STRUCT<kind:STRING, label:STRING, ref:STRING>>,
  trace_id          STRING,
  model             STRING,
  prompt_tokens     INT,
  completion_tokens INT,
  latency_ms        INT,
  cost_usd          DECIMAL(10,6),
  fallback_step     INT,
  created_at        TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.app.tool_call (
  tool_call_id      STRING NOT NULL,
  session_id        STRING NOT NULL,
  message_id        STRING NOT NULL,
  tool_name         STRING NOT NULL,
  args_json         STRING NOT NULL,
  result_preview    STRING,
  result_size_bytes INT,
  latency_ms        INT,
  error_code        STRING,
  retriable         BOOLEAN,
  attempt           INT,
  started_at        TIMESTAMP NOT NULL,
  ended_at          TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.app.feedback (
  feedback_id       STRING NOT NULL,
  session_id        STRING NOT NULL,
  message_id        STRING,
  user_id           STRING NOT NULL,
  thumbs            STRING,
  rating            INT,
  reason_codes      ARRAY<STRING>,
  free_text         STRING,
  created_at        TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.app.decision_log (
  decision_log_id   STRING NOT NULL,
  claim_id          STRING NOT NULL,
  session_id        STRING NOT NULL,
  adjuster_id       STRING NOT NULL,
  agent_recommendation STRING,
  agent_reasoning_md STRING,
  cited_clauses     ARRAY<STRING>,
  cited_kb          ARRAY<STRING>,
  cited_claims_anon ARRAY<STRING>,
  adjuster_concurred BOOLEAN,
  adjuster_final_decision STRING,
  adjuster_override_reason STRING,
  model             STRING NOT NULL,
  agent_version     STRING NOT NULL,
  trace_id          STRING NOT NULL,
  superseded_by     STRING,
  superseded_reason STRING,
  created_at        TIMESTAMP NOT NULL
) USING DELTA
TBLPROPERTIES (delta.enableChangeDataFeed = true);

CREATE TABLE IF NOT EXISTS __CATALOG__.app.escalation (
  escalation_id     STRING NOT NULL,
  claim_id          STRING NOT NULL,
  reason            STRING NOT NULL,
  queue             STRING NOT NULL,
  note              STRING,
  created_by        STRING NOT NULL,
  created_at        TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.app.feature_flag (
  flag_key          STRING NOT NULL,
  market            STRING,
  role              STRING,
  percentage        INT,
  enabled           BOOLEAN NOT NULL,
  updated_at        TIMESTAMP NOT NULL,
  updated_by        STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS __CATALOG__.eval.golden_dataset_claimscopilot (
  example_id        STRING NOT NULL,
  category          STRING NOT NULL,
  difficulty        INT,
  claim_id          STRING,
  adjuster_query    STRING NOT NULL,
  language          STRING NOT NULL,
  expected_tools    ARRAY<STRING>,
  expected_decision_class STRING,
  expected_citations ARRAY<STRING>,
  expected_refusal  BOOLEAN,
  expected_escalation STRING,
  reference_answer  STRING,
  rubric_notes      STRING,
  fixture_snapshot  STRING,
  added_at          TIMESTAMP,
  added_by          STRING
) USING DELTA;

-- =============================================================================
-- UC FUNCTIONS (tools)
-- =============================================================================

CREATE OR REPLACE FUNCTION __CATALOG__.tools.get_policy_terms(p_policy_id STRING)
RETURNS STRING
COMMENT 'Return canonical coverage terms for a policy as JSON. Read-only.'
RETURN (
  SELECT TO_JSON(NAMED_STRUCT(
    'policy_id', p.policy_id,
    'coverages', COLLECT_LIST(NAMED_STRUCT(
       'coverage_type', c.coverage_type,
       'sum_insured', c.sum_insured,
       'currency', c.currency_code,
       'excess', c.excess,
       'included_perils', c.included_perils,
       'excluded_perils', c.excluded_perils)),
    'wording_doc_id', MAX(c.wording_doc_id),
    'wording_version', MAX(c.wording_version)
  ))
  FROM __CATALOG__.policy.policy p
  JOIN __CATALOG__.policy.coverage c USING (policy_id)
  WHERE p.policy_id = p_policy_id
  GROUP BY p.policy_id
);

CREATE OR REPLACE FUNCTION __CATALOG__.tools.get_claim(p_claim_id STRING)
RETURNS STRING
COMMENT 'Return canonical claim header as JSON.'
RETURN (
  SELECT ANY_VALUE(TO_JSON(STRUCT(*)))
  FROM __CATALOG__.claims.claim
  WHERE claim_id = p_claim_id
);

CREATE OR REPLACE FUNCTION __CATALOG__.tools.get_claim_events(p_claim_id STRING, p_lookback_days INT)
RETURNS STRING
COMMENT 'Return claim event log within lookback_days as JSON array.'
RETURN (
  SELECT TO_JSON(COLLECT_LIST(STRUCT(*)))
  FROM __CATALOG__.claims.claim_event
  WHERE claim_id = p_claim_id
    AND event_ts >= TIMESTAMPADD(DAY, -p_lookback_days, CURRENT_TIMESTAMP())
);

CREATE OR REPLACE FUNCTION __CATALOG__.tools.get_claim_history(p_customer_id STRING, p_lookback_days INT)
RETURNS STRING
COMMENT 'Return claims for a customer within lookback_days as JSON array.'
RETURN (
  SELECT TO_JSON(COLLECT_LIST(NAMED_STRUCT(
    'claim_id', claim_id,
    'policy_id', policy_id,
    'device_id', device_id,
    'claim_type', claim_type,
    'status', status,
    'paid_amount', paid_amount,
    'fraud_score', fraud_score,
    'decision_made_at', decision_made_at
  )))
  FROM (
    SELECT *
    FROM __CATALOG__.claims.claim
    WHERE policyholder_id = p_customer_id
      AND fnol_timestamp >= TIMESTAMPADD(DAY, -p_lookback_days, CURRENT_TIMESTAMP())
    ORDER BY fnol_timestamp DESC
    LIMIT 50
  )
);

CREATE OR REPLACE FUNCTION __CATALOG__.tools.get_device(p_device_id STRING)
RETURNS STRING
COMMENT 'Return device master record as JSON.'
RETURN (
  SELECT ANY_VALUE(TO_JSON(STRUCT(*)))
  FROM __CATALOG__.devices.device
  WHERE device_id = p_device_id
);

CREATE OR REPLACE FUNCTION __CATALOG__.tools.get_repair_order(p_repair_order_id STRING)
RETURNS STRING
COMMENT 'Return repair order record as JSON.'
RETURN (
  SELECT ANY_VALUE(TO_JSON(STRUCT(*)))
  FROM __CATALOG__.repairs.repair_order
  WHERE repair_order_id = p_repair_order_id
);

CREATE OR REPLACE FUNCTION __CATALOG__.tools.compute_excess(p_claim_id STRING)
RETURNS STRING
COMMENT 'Compute deterministic excess due on a claim as JSON {amount, currency, breakdown}.'
RETURN (
  SELECT ANY_VALUE(TO_JSON(NAMED_STRUCT(
    'amount', p.excess_amount,
    'currency', p.currency_code,
    'breakdown', NAMED_STRUCT(
      'policy_excess', p.excess_amount,
      'claim_count_surcharge', 0.00
    )
  )))
  FROM __CATALOG__.claims.claim c
  JOIN __CATALOG__.policy.policy p USING (policy_id)
  WHERE c.claim_id = p_claim_id
);

CREATE OR REPLACE FUNCTION __CATALOG__.tools.estimate_repair_cost(p_device_id STRING, p_repair_type STRING, p_country STRING)
RETURNS STRING
COMMENT 'Return p25/p50/p75 of repair cost for device+type+country (last 90 days) as JSON.'
RETURN (
  SELECT TO_JSON(NAMED_STRUCT(
    'p25', PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY r.invoiced_amount),
    'p50', PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY r.invoiced_amount),
    'p75', PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY r.invoiced_amount),
    'currency', MAX(d.purchase_country),
    'n_observations', COUNT(*)
  ))
  FROM __CATALOG__.repairs.repair_order r
  JOIN __CATALOG__.devices.device d USING (device_id)
  WHERE r.repair_type = p_repair_type
    AND r.vendor_country = p_country
    AND d.make  = (SELECT ANY_VALUE(make)  FROM __CATALOG__.devices.device WHERE device_id = p_device_id)
    AND d.model = (SELECT ANY_VALUE(model) FROM __CATALOG__.devices.device WHERE device_id = p_device_id)
    AND r.completed_ts >= CURRENT_TIMESTAMP() - INTERVAL 90 DAYS
);

CREATE OR REPLACE FUNCTION __CATALOG__.tools.check_partner_sla(p_partner_id STRING, p_claim_id STRING)
RETURNS STRING
COMMENT 'Return SLA status for a claim against the partner contract as JSON.'
RETURN (
  SELECT ANY_VALUE(TO_JSON(NAMED_STRUCT(
    'cycle_time_hours_so_far',
      (UNIX_TIMESTAMP(CURRENT_TIMESTAMP()) - UNIX_TIMESTAMP(c.fnol_timestamp)) / 3600.0,
    'sla_target_hours', 48,
    'status', CASE
      WHEN (UNIX_TIMESTAMP(CURRENT_TIMESTAMP()) - UNIX_TIMESTAMP(c.fnol_timestamp)) / 3600.0 < 36 THEN 'ON_TRACK'
      WHEN (UNIX_TIMESTAMP(CURRENT_TIMESTAMP()) - UNIX_TIMESTAMP(c.fnol_timestamp)) / 3600.0 < 48 THEN 'AT_RISK'
      ELSE 'BREACHED'
    END,
    'contract_clause', CONCAT('contract-', pa.partner_id, '#sla_section_4')
  )))
  FROM __CATALOG__.claims.claim c
  JOIN __CATALOG__.partners.partner_account pa ON pa.partner_id = p_partner_id
  WHERE c.claim_id = p_claim_id
);


-- =============================================================================
-- Grants live in setup/02_grants.sql — run that after this file completes,
-- with the service principal name substituted. For dev deploys, skip 02.
-- =============================================================================

-- Verify with:
--   SHOW TABLES IN __CATALOG__.policy;
--   SHOW FUNCTIONS IN __CATALOG__.tools;
--   SELECT * FROM __CATALOG__.app.feature_flag;
