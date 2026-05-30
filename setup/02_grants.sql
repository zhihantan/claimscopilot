-- =============================================================================
-- ClaimsCopilot — Grants for the App service principal
-- =============================================================================
--
-- Run AFTER setup/01_ddl.sql, AFTER you have provisioned the service
-- principal that the App will run as in `stage`/`prod` targets. Skip this
-- file entirely for `dev` deploys — the App runs as the deploying user.
--
-- `__CATALOG__` and `__APP_SP__` are placeholders: scripts/grant_app_sp.py
-- substitutes the target catalog and the App's service-principal client id
-- (fetched from `databricks apps get`) and runs this file. Grants are bucketed
-- by catalog/schema/table/function so partial failure is easy to debug.
-- =============================================================================

-- Catalog access
GRANT USE CATALOG ON CATALOG __CATALOG__ TO `__APP_SP__`;

-- Schema access
GRANT USE SCHEMA ON SCHEMA __CATALOG__.policy   TO `__APP_SP__`;
GRANT USE SCHEMA ON SCHEMA __CATALOG__.claims   TO `__APP_SP__`;
GRANT USE SCHEMA ON SCHEMA __CATALOG__.devices  TO `__APP_SP__`;
GRANT USE SCHEMA ON SCHEMA __CATALOG__.repairs  TO `__APP_SP__`;
GRANT USE SCHEMA ON SCHEMA __CATALOG__.partners TO `__APP_SP__`;
GRANT USE SCHEMA ON SCHEMA __CATALOG__.kb       TO `__APP_SP__`;
GRANT USE SCHEMA ON SCHEMA __CATALOG__.tools    TO `__APP_SP__`;
GRANT USE SCHEMA ON SCHEMA __CATALOG__.app        TO `__APP_SP__`;
GRANT USE SCHEMA ON SCHEMA __CATALOG__.eval       TO `__APP_SP__`;
GRANT USE SCHEMA ON SCHEMA __CATALOG__.indexes    TO `__APP_SP__`;

-- Read tables
GRANT SELECT ON TABLE __CATALOG__.policy.policy             TO `__APP_SP__`;
GRANT SELECT ON TABLE __CATALOG__.policy.coverage           TO `__APP_SP__`;
GRANT SELECT ON TABLE __CATALOG__.policy.wording_document   TO `__APP_SP__`;
GRANT SELECT ON TABLE __CATALOG__.policy.wording_chunk      TO `__APP_SP__`;
GRANT SELECT ON TABLE __CATALOG__.claims.claim              TO `__APP_SP__`;
GRANT SELECT ON TABLE __CATALOG__.claims.claim_event        TO `__APP_SP__`;
-- __CATALOG__.claims.narrative_anon — DLT-managed; grant after first pipeline run.
GRANT SELECT ON TABLE __CATALOG__.devices.device            TO `__APP_SP__`;
GRANT SELECT ON TABLE __CATALOG__.repairs.repair_order      TO `__APP_SP__`;
GRANT SELECT ON TABLE __CATALOG__.partners.partner_account  TO `__APP_SP__`;
GRANT SELECT ON TABLE __CATALOG__.kb.article                TO `__APP_SP__`;
GRANT SELECT ON TABLE __CATALOG__.kb.article_chunk          TO `__APP_SP__`;

-- Write tables
GRANT SELECT, MODIFY ON TABLE __CATALOG__.app.session       TO `__APP_SP__`;
GRANT SELECT, MODIFY ON TABLE __CATALOG__.app.message       TO `__APP_SP__`;
GRANT SELECT, MODIFY ON TABLE __CATALOG__.app.tool_call     TO `__APP_SP__`;
GRANT SELECT, MODIFY ON TABLE __CATALOG__.app.feedback      TO `__APP_SP__`;
GRANT SELECT, MODIFY ON TABLE __CATALOG__.app.decision_log  TO `__APP_SP__`;
GRANT SELECT, MODIFY ON TABLE __CATALOG__.app.escalation    TO `__APP_SP__`;
GRANT SELECT ON TABLE          __CATALOG__.app.feature_flag TO `__APP_SP__`;
GRANT SELECT ON TABLE          __CATALOG__.eval.golden_dataset_claimscopilot TO `__APP_SP__`;

-- Tool functions
GRANT EXECUTE ON FUNCTION __CATALOG__.tools.get_policy_terms     TO `__APP_SP__`;
GRANT EXECUTE ON FUNCTION __CATALOG__.tools.get_claim            TO `__APP_SP__`;
GRANT EXECUTE ON FUNCTION __CATALOG__.tools.get_claim_events     TO `__APP_SP__`;
GRANT EXECUTE ON FUNCTION __CATALOG__.tools.get_claim_history    TO `__APP_SP__`;
GRANT EXECUTE ON FUNCTION __CATALOG__.tools.get_device           TO `__APP_SP__`;
GRANT EXECUTE ON FUNCTION __CATALOG__.tools.get_repair_order     TO `__APP_SP__`;
GRANT EXECUTE ON FUNCTION __CATALOG__.tools.compute_excess       TO `__APP_SP__`;
GRANT EXECUTE ON FUNCTION __CATALOG__.tools.estimate_repair_cost TO `__APP_SP__`;
GRANT EXECUTE ON FUNCTION __CATALOG__.tools.check_partner_sla    TO `__APP_SP__`;

-- Deferred (run again after the narrative_anonymization DLT pipeline produces the table):
-- GRANT SELECT ON TABLE __CATALOG__.claims.narrative_anon TO `__APP_SP__`;
