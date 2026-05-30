"""ClaimsCopilot — Narrative Anonymization (DLT).

Reads finalized claims from `<catalog>.claims.claim`, redacts PII from
the narrative via `ai_mask` plus a Japanese-specific postal/address regex pass,
anonymizes the claim id, buckets paid amounts into bands, and emits one row per
claim into the DLT-managed `<catalog>.claims.narrative_anon` table. The
Delta-sync Vector Search index `<catalog>.indexes.claim_narratives`
sources from this table.

Pipeline config lives in `resources/pipelines.yml`. The pipeline targets
catalog `<catalog>`, schema `claims`.

Note: do not add the `# Databricks notebook source` header — DAB will then
convert this to a workspace notebook entity, and DLT's NO_TABLES_IN_PIPELINE
check does not see the `@dlt.table` decorators inside a notebook-typed file
when referenced as a file resource. Plain `.py` source works.

Japanese PII: `ai_mask` catches names/phones reliably but its NER misses
numeric postal-code fragments (e.g. `〒150-0001`) and street-number fragments
(`1丁目2番3号`). The canonical regex patterns for that gap live in the sibling
module `jp_pii.py` (dependency-free, unit-tested in tests/test_jp_pii.py) and
are applied below with native `regexp_replace`.
"""

import os
import sys

import dlt
from pyspark.sql.functions import (
    coalesce,
    col,
    current_timestamp,
    expr,
    lit,
    regexp_replace,
    sha2,
    when,
)

# DAB syncs the whole project tree, so jp_pii.py sits next to this file in the
# workspace. Add our own directory to sys.path so `import jp_pii` resolves when
# DLT executes this library at pipeline runtime.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from jp_pii import (  # noqa: E402
    ADDRESS_PLACEHOLDER,
    JP_ADDRESS_NUM_PATTERN,
    JP_POSTAL_BARE_PATTERN,
    JP_POSTAL_MARK_PATTERN,
    POSTAL_PLACEHOLDER,
)

# Catalog is injected by the pipeline `configuration` block in
# resources/pipelines.yml (`catalog: ${var.catalog_lake}`), so this pipeline is
# not tied to any one workspace. Falls back to "main" for ad-hoc runs.
CATALOG = spark.conf.get("catalog", "main")  # noqa: F821 (spark is a DLT global)

# Only anonymize claims that have reached a terminal status — in-flight
# claims may still get edits to the narrative and shouldn't feed precedent
# search yet.
_TERMINAL_STATUSES = ("CLOSED", "RESOLVED", "PAID", "DENIED", "PARTIALLY_PAID")


@dlt.view(name="claims_finalized")
def claims_finalized():
    return (
        spark.readStream.table(f"{CATALOG}.claims.claim")
        .filter(col("status").isin(*_TERMINAL_STATUSES))
        .filter(col("incident_description_raw").isNotNull())
    )


@dlt.table(
    name="narrative_anon",
    comment="PII-redacted claim narratives for precedent retrieval.",
    table_properties={
        "delta.enableChangeDataFeed": "true",
        "pipelines.autoOptimize.managed": "true",
    },
)
@dlt.expect("non_empty_narrative", "narrative_anon IS NOT NULL AND length(narrative_anon) > 5")
@dlt.expect_or_drop("language_in_pilot", "language IN ('en','es','ja')")
def narrative_anon():
    claims = dlt.read_stream("claims_finalized").alias("c")
    devices = spark.table(f"{CATALOG}.devices.device").alias("d")

    language_col = coalesce(
        col("c.incident_description_lang"), col("c.language_pref"), lit("en")
    )

    # Pass 1 — ai_mask: names, emails, phones, organizations, and most addresses.
    masked = expr(
        "ai_mask(coalesce(c.incident_description_en, c.incident_description_raw), "
        "array('person', 'email', 'phone', 'address', 'organization'))"
    )
    # Pass 2 (all languages) — 〒-marked postal codes. The 〒 mark is JP-only, so
    # this is safe to run on every row.
    masked = regexp_replace(masked, JP_POSTAL_MARK_PATTERN, POSTAL_PLACEHOLDER)
    # Pass 3 (JP rows only) — bare postal codes + street-number fragments that
    # NER leaves behind. Scoped to `ja` so we don't over-mask en/es narratives.
    masked_ja = regexp_replace(masked, JP_POSTAL_BARE_PATTERN, POSTAL_PLACEHOLDER)
    masked_ja = regexp_replace(masked_ja, JP_ADDRESS_NUM_PATTERN, ADDRESS_PLACEHOLDER)
    narrative_col = when(language_col == "ja", masked_ja).otherwise(masked)

    return (
        claims.join(devices, col("c.device_id") == col("d.device_id"), "left")
        .select(
            sha2(col("c.claim_id"), 256).substr(1, 16).alias("claim_id_anon"),
            coalesce(col("c.decision_reason_code"), col("c.status")).alias("decision"),
            when(col("c.paid_amount") < 250, lit("LOW"))
            .when(col("c.paid_amount") < 1500, lit("MED"))
            .otherwise(lit("HIGH"))
            .alias("paid_band"),
            col("c.claim_type").alias("claim_type"),
            coalesce(col("d.device_category"), lit("UNKNOWN")).alias("product_code"),
            language_col.alias("language"),
            narrative_col.alias("narrative_anon"),
            current_timestamp().alias("_ingest_ts"),
        )
    )
