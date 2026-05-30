#!/usr/bin/env bash
# Create the Lakebase (Autoscaling) Postgres project that backs ClaimsCopilot's
# durable agent checkpointer (CC_CHECKPOINTER=lakebase).
#
# Prereqs:
#   - Databricks CLI >= 0.285.0          (databricks --version)
#   - An authenticated CLI profile for your workspace:
#       databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile <profile>
#   - psql + jq on PATH
#
# The agent calls AsyncPostgresSaver.setup() on boot to create the checkpoint
# tables, so this script only provisions the project/endpoint/database.
set -euo pipefail

PROFILE="${DATABRICKS_PROFILE:-DEFAULT}"
PROJECT="${LAKEBASE_PROJECT:-claimscopilot}"
BRANCH="production"
ENDPOINT="primary"
DBNAME="${LAKEBASE_DB:-claimscopilot}"
ENDPOINT_PATH="projects/${PROJECT}/branches/${BRANCH}/endpoints/${ENDPOINT}"

echo "==> Creating Lakebase project '${PROJECT}' (profile=${PROFILE})"
databricks postgres create-project "${PROJECT}" \
  --json '{"spec": {"display_name": "ClaimsCopilot checkpointer"}}' \
  -p "${PROFILE}" || echo "    (project may already exist — continuing)"

echo "==> Waiting for the primary endpoint to become ACTIVE ..."
for _ in $(seq 1 30); do
  STATE=$(databricks postgres list-endpoints "projects/${PROJECT}/branches/${BRANCH}" \
    -p "${PROFILE}" -o json | jq -r '.[0].status.current_state // "UNKNOWN"')
  echo "    endpoint state: ${STATE}"
  [ "${STATE}" = "ACTIVE" ] && break
  sleep 10
done

HOST=$(databricks postgres list-endpoints "projects/${PROJECT}/branches/${BRANCH}" \
  -p "${PROFILE}" -o json | jq -r '.[0].status.hosts.host')

echo "==> Creating database '${DBNAME}' (idempotent)"
TOKEN=$(databricks postgres generate-database-credential "${ENDPOINT_PATH}" \
  -p "${PROFILE}" -o json | jq -r '.token')
EMAIL=$(databricks current-user me -p "${PROFILE}" -o json | jq -r '.userName')
PGPASSWORD="${TOKEN}" psql "host=${HOST} port=5432 dbname=postgres user=${EMAIL} sslmode=require" \
  -c "CREATE DATABASE ${DBNAME};" 2>/dev/null || echo "    (database may already exist — continuing)"

cat <<EOF

================================================================
Lakebase ready. Wire ClaimsCopilot to it:

1) Attach the project to the App as a database resource
   (Databricks UI: App > Edit > Resources > Database). The runtime then
   injects PGHOST / PGUSER / PGPORT / PGDATABASE.

2) In app.yaml set:
     CC_CHECKPOINTER = "lakebase"
     ENDPOINT_NAME   = "${ENDPOINT_PATH}"
     PGDATABASE      = "${DBNAME}"     # only if not using the resource default
   (PGHOST for reference: ${HOST})

3) Bump databricks-sdk in requirements.txt to a version exposing w.postgres,
   then redeploy:  databricks bundle deploy --target dev

The App SP (PGUSER) must have CREATE on database '${DBNAME}' so the agent's
saver.setup() can create the checkpoint tables on first boot.
================================================================
EOF
