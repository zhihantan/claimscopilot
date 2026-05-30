"""Seed realistic synthetic data into Unity Catalog for demo + eval.

Usage (interactive):
    databricks bundle run seed_data_job

Usage (local against a workspace):
    python -m data.seed_synthetic --rows 5000 --languages en,es,ja

Seeded deterministically (`--seed`) so test fixtures are stable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from databricks.sdk import WorkspaceClient
from faker import Faker

PARTNERS = [
    ("partner-mno-vodafone-gb",  "Telco Alpha GB",  "MNO",       "GB"),
    ("partner-retail-johnlewis-gb","Retail Beta GB",  "RETAILER",  "GB"),
    ("partner-bank-nu-mx",       "Bank Gamma MX",   "BANK",      "MX"),
    ("partner-mno-rakuten-jp",   "Telco Delta JP",  "MNO",       "JP"),
    ("partner-oem-foxmobile-jp", "OEM Epsilon JP",  "OEM",       "JP"),
]

DEVICE_MODELS = [
    ("FoxMobile", "F25 Ultra", 2025, "SMARTPHONE", 1299.00),
    ("FoxMobile", "F23 Lite",  2023, "SMARTPHONE",  399.00),
    ("Lyra",      "Pixel-9",   2024, "SMARTPHONE",  899.00),
    ("Nimbus",    "TabPro 12", 2024, "TABLET",      749.00),
]

CLAIM_TYPES = ["SCREEN", "ADP", "LIQUID", "THEFT", "SOFTWARE_FAULT"]
NARR_TEMPLATES = {
    "en": {
        "SCREEN": [
            "I dropped my phone on the platform and the screen cracked across the top. Touch works but the bottom corner is unresponsive.",
            "My phone fell while running and the display shattered. There's a crack from corner to corner.",
        ],
        "ADP": [
            "The phone slipped from my hand on the stairs. The back glass is cracked and the screen is also damaged.",
            "I dropped the device while taking a photo on holiday. There's visible damage on both the screen and the side.",
        ],
        "LIQUID": [
            "Coffee spilled on my phone while I was at the cafe. After 10 minutes the screen went black and now it won't turn on.",
        ],
        "THEFT": [
            "My phone was stolen on the bus. I reported it to the police, reference number 2026/45/789.",
        ],
        "SOFTWARE_FAULT": [
            "The phone keeps restarting itself every few minutes. I haven't dropped it. The screen is fine.",
        ],
    },
    "es": {
        "SCREEN": [
            "Se me cayó el teléfono en el andén y la pantalla se rajó en la parte superior. El táctil funciona pero la esquina inferior no responde.",
        ],
        "ADP": [
            "El teléfono se me resbaló de la mano en las escaleras. El cristal trasero está roto y la pantalla también dañada.",
        ],
        "LIQUID": [
            "Se derramó café sobre el teléfono en la cafetería. A los 10 minutos la pantalla se apagó y ya no enciende.",
        ],
        "THEFT": [
            "Me robaron el teléfono en el autobús. Lo denuncié, número de referencia 2026/45/789.",
        ],
        "SOFTWARE_FAULT": [
            "El teléfono se reinicia solo cada pocos minutos. No se me ha caído. La pantalla está bien.",
        ],
    },
    "ja": {
        "SCREEN": [
            "駅のホームで携帯を落として、画面の上部にひびが入りました。タッチは動きますが、下の角は反応しません。",
        ],
        "ADP": [
            "階段で手を滑らせて落としてしまいました。背面ガラスが割れ、画面も損傷しています。",
        ],
        "LIQUID": [
            "カフェでコーヒーをこぼしてしまい、10分後に画面が消えてもう電源が入りません。",
        ],
        "THEFT": [
            "バスで携帯を盗まれました。警察に通報済み、受理番号2026/45/789。",
        ],
        "SOFTWARE_FAULT": [
            "携帯が数分おきに勝手に再起動します。落としていません。画面は正常です。",
        ],
    },
}

WORDING_SECTIONS = [
    ("1", "Definitions", "Defines key terms used in this policy."),
    ("2", "Cover", "Outlines what is covered."),
    ("3.1", "Accidental damage", "Cover for unexpected and unintended damage."),
    ("3.2", "Cracked or broken screen", (
        "We will pay to repair or replace the screen of an insured device that "
        "has been accidentally damaged (e.g. dropped, struck). You must pay the "
        "excess shown in the policy schedule. Cosmetic-only damage that does not "
        "affect functionality may be excluded under section 6.3."
    )),
    ("3.3", "Liquid damage", "Cover for accidental ingress of liquid into the device."),
    ("4",   "Theft", "Cover for theft following forcible or violent entry, or pickpocketing reported to police within 24 hours."),
    ("5",   "Excess", "The customer excess shown on the policy schedule applies per claim."),
    ("6.1", "Exclusion — pre-existing damage", "Damage occurring before the policy effective date is not covered."),
    ("6.3", "Exclusion — cosmetic only", "Damage that is purely cosmetic and does not affect functionality is not covered."),
    ("6.5", "Exclusion — unattended theft", "Theft of devices left unattended in public is not covered."),
    ("7",   "Claims procedure", "How to make a claim, including IMEI and proof requirements."),
]


@dataclass
class Args:
    rows: int
    seed: int
    languages: list[str]
    catalog_lake: str
    catalog_ai: str
    warehouse_id: str
    workspace_host: str
    token: str | None


def parse_args() -> Args:
    import os

    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=5000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--languages", type=str, default="en,es,ja")
    p.add_argument("--catalog-lake", type=str, default="main")
    p.add_argument("--catalog-ai", type=str, default="main")
    p.add_argument("--warehouse-id", type=str, required=True)
    p.add_argument(
        "--workspace-host",
        type=str,
        default=os.environ.get("DATABRICKS_HOST"),
        help="Defaults to $DATABRICKS_HOST (auto-set inside Databricks jobs).",
    )
    p.add_argument("--token", type=str, default=os.environ.get("DATABRICKS_TOKEN"))
    a = p.parse_args()
    if not a.workspace_host:
        p.error("--workspace-host is required when $DATABRICKS_HOST is not set")
    return Args(
        rows=a.rows, seed=a.seed, languages=a.languages.split(","),
        catalog_lake=a.catalog_lake, catalog_ai=a.catalog_ai,
        warehouse_id=a.warehouse_id, workspace_host=a.workspace_host, token=a.token,
    )


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def gen(args: Args) -> dict[str, list[tuple]]:
    random.seed(args.seed)
    fake = Faker()
    Faker.seed(args.seed)

    out: dict[str, list[tuple]] = {
        "partners": [], "devices": [], "policies": [], "coverages": [],
        "claims": [], "wording_documents": [], "wording_chunks": [],
        "kb_articles": [], "kb_chunks": [], "narrative_anon": [],
    }

    # Partners
    for pid, pname, ptype, country in PARTNERS:
        out["partners"].append((
            pid, pname, ptype, None,
            "EMEA" if country in ("GB",) else ("APAC" if country in ("JP",) else "AMER"),
            country, "2024-01-01", 12.50, "{}", "{}", "am-001", "se-001",
            f"contract-{pid}", "ACTIVE", "2026-04-15",
        ))

    # Wording documents (one per product per country per language)
    for country, lang in [("GB", "en"), ("MX", "es"), ("JP", "ja")]:
        doc_id = f"WORD-{country}-MOBILE-FULL-V2025-04"
        version = "v2025-04"
        full_text = "\n\n".join(f"{p} {t}: {body}" for p, t, body in WORDING_SECTIONS)
        out["wording_documents"].append((
            doc_id, "MOBILE_FULL", country, lang, version,
            "2025-04-01", None, f"uc://wordings/{doc_id}.pdf", full_text,
            sha256(full_text), datetime.now(timezone.utc).isoformat(),
        ))
        prev_id = None
        chunks: list[str] = []
        for sec_path, title, body in WORDING_SECTIONS:
            chunk_id = f"{doc_id}#sec={sec_path}"
            chunks.append(chunk_id)
            out["wording_chunks"].append((
                chunk_id, doc_id, version, sec_path, title,
                f"[Section: {sec_path} — \"{title}\"]\n{body}",
                len(body) // 4, lang, "MOBILE_FULL", country,
                prev_id, None,
                datetime.now(timezone.utc).isoformat(),
            ))
            prev_id = chunk_id
        # next_chunk_id post-fix
        for i, cid in enumerate(chunks[:-1]):
            for row in out["wording_chunks"]:
                if row[0] == cid:
                    row_list = list(row); row_list[11] = chunks[i + 1]
                    out["wording_chunks"][out["wording_chunks"].index(row)] = tuple(row_list)
                    break

    # KB articles (small, per language)
    kb_examples = [
        ("1187", "How to verify a cracked screen claim",
         "Confirm IMEI matches the policy; check that the damage is consistent with the narrative; "
         "request a photo of the IMEI plate. If pre-existing damage suspected, refer to L3."),
        ("1188", "Pre-existing damage indicators",
         "Look for inconsistencies between the FNOL date and visible wear, conflicting photo metadata, "
         "or repeat claims within 6 months. Flag for SIU when fraud_score >= 0.7."),
    ]
    for art_id, title, body in kb_examples:
        for lang in args.languages:
            out["kb_articles"].append((
                art_id, title, body, lang, "claims-playbook",
                json.dumps(["MOBILE_FULL"]), json.dumps(["GB", "MX", "JP"]),
                "v2025-04", datetime.now(timezone.utc).isoformat(),
            ))
            out["kb_chunks"].append((
                f"kb-{art_id}-{lang}-001", art_id, lang, "1", body,
                len(body) // 4, datetime.now(timezone.utc).isoformat(),
            ))

    # Policies / Claims
    for i in range(args.rows):
        country_lang = random.choice([("GB", "en"), ("MX", "es"), ("JP", "ja")])
        country, lang = country_lang
        if lang not in args.languages:
            continue
        partner = next(p for p in PARTNERS if p[3] == country)
        make, model, model_year, dev_cat, repl = random.choice(DEVICE_MODELS)
        device_id = f"dev-{uuid.uuid4().hex[:12]}"
        imei = f"{random.randint(10**14, 10**15-1)}"
        out["devices"].append((
            device_id, sha256(imei), sha256(uuid.uuid4().hex),
            make, model, model_year, dev_cat,
            "oem-foxmobile" if make == "FoxMobile" else f"oem-{make.lower()}",
            country, (datetime.now() - timedelta(days=random.randint(30, 600))).date().isoformat(),
            repl, repl,
            (datetime.now() + timedelta(days=random.randint(30, 365))).date().isoformat(),
            None, "IN_SERVICE", None,
        ))

        policy_id = f"pol-{uuid.uuid4().hex[:12]}"
        customer_id = f"cust-{uuid.uuid4().hex[:8]}"
        effective_date = datetime.now() - timedelta(days=random.randint(30, 360))
        out["policies"].append((
            policy_id, f"POL-{country}-2026-{i:06d}",
            partner[0], "MOBILE_FULL", customer_id, device_id,
            effective_date.date().isoformat(),
            (effective_date + timedelta(days=365)).date().isoformat(),
            12, 9.99, {"GB": "GBP", "MX": "MXN", "JP": "JPY"}[country],
            {"GB": 49.00, "MX": 1000.00, "JP": 8000.00}[country],
            1500.00, 0, "ACTIVE", country, lang,
            "EMEA" if country == "GB" else ("APAC" if country == "JP" else "AMER"),
            None, None, None,
            effective_date.isoformat(), effective_date.isoformat(),
            "INTERNAL_PA_V2", datetime.now(timezone.utc).isoformat(),
        ))
        out["coverages"].append((
            f"cov-{uuid.uuid4().hex[:10]}", policy_id, "SCREEN_DAMAGE", 1500.00,
            {"GB": "GBP", "MX": "MXN", "JP": "JPY"}[country],
            {"GB": 49.00, "MX": 1000.00, "JP": 8000.00}[country],
            json.dumps(["ACCIDENTAL_SCREEN", "CRACKED_DISPLAY"]),
            json.dumps(["PRE_EXISTING", "COSMETIC_ONLY"]),
            json.dumps([]),
            effective_date.date().isoformat(),
            (effective_date + timedelta(days=365)).date().isoformat(),
            f"WORD-{country}-MOBILE-FULL-V2025-04", "v2025-04", None,
        ))

        if random.random() < 0.4:
            ctype = random.choice(CLAIM_TYPES)
            narr = random.choice(NARR_TEMPLATES[lang][ctype])
            claim_id = f"clm-{uuid.uuid4().hex[:12]}"
            fnol = datetime.now() - timedelta(days=random.randint(0, 90))
            fraud = round(min(1.0, max(0.0, random.gauss(0.2, 0.18))), 2)
            decision = random.choices(["APPROVE", "DENY", "PARTIAL"], weights=[7, 2, 1])[0]
            out["claims"].append((
                claim_id, f"CLM-{country}-2026-{i:07d}", policy_id,
                customer_id, device_id, "APP", fnol.isoformat(),
                (fnol - timedelta(hours=random.randint(0, 48))).isoformat(),
                country, narr, lang, narr if lang == "en" else f"[TRANSLATED FROM {lang}] {narr}",
                ctype, "STANDARD", "OPEN",
                f"adj-l2-{random.randint(1, 50):03d}", None,
                None, None, {"GB": "GBP", "MX": "MXN", "JP": "JPY"}[country],
                None, fraud, "fraud-v3.4",
                None, None, None, lang,
                "EMEA" if country == "GB" else ("APAC" if country == "JP" else "AMER"),
                fnol.isoformat(), fnol.isoformat(),
            ))
            # narrative_anon for VS
            anon_narr = narr.replace("2026/45/789", "<<CASE_REF>>")
            paid_band = "0-100" if decision == "DENY" else ("100-300" if decision == "PARTIAL" else "300-700")
            out["narrative_anon"].append((
                sha256(claim_id)[:16], decision, paid_band, ctype,
                "MOBILE_FULL", lang, anon_narr,
                datetime.now(timezone.utc).isoformat(),
            ))

    return out


_DDL_INSERT_BATCHES = {
    # (table_qualifier, column_list, key in `out`)
    "policy.policy": (
        "policy_id, policy_number, partner_id, product_code, policyholder_id, device_id, "
        "effective_date, expiry_date, term_months, premium_amount, currency_code, excess_amount, "
        "coverage_limit_total, claims_used_count, status, issue_country, language_pref, "
        "data_residency_region, cancellation_date, cancellation_reason_code, last_endorsement_id, "
        "created_at, updated_at, source_system, _ingest_ts",
        "policies",
    ),
    "policy.coverage": (
        "coverage_id, policy_id, coverage_type, sum_insured, currency_code, excess, "
        "included_perils, excluded_perils, endorsements, effective_from, effective_to, "
        "wording_doc_id, wording_version, clauses_struct",
        "coverages",
    ),
    "policy.wording_document": (
        "wording_doc_id, product_code, country, language, version, "
        "effective_from, effective_to, source_uri, text_full, hash_sha256, created_at",
        "wording_documents",
    ),
    "policy.wording_chunk": (
        "chunk_id, wording_doc_id, version, section_path, section_title, "
        "text, token_count, language, product_code, country, "
        "prev_chunk_id, next_chunk_id, _ingest_ts",
        "wording_chunks",
    ),
    "claims.claim": (
        "claim_id, claim_number, policy_id, policyholder_id, device_id, fnol_channel, "
        "fnol_timestamp, incident_timestamp, incident_country, incident_description_raw, "
        "incident_description_lang, incident_description_en, claim_type, triage_class, status, "
        "assigned_adjuster_id, repair_order_id, paid_amount, reserve_amount, currency_code, "
        "decision_reason_code, fraud_score, fraud_score_version, decision_made_at, resolution_at, "
        "cycle_time_hours, language_pref, data_residency_region, created_at, updated_at",
        "claims",
    ),
    "claims.narrative_anon": (
        "claim_id_anon, decision, paid_band, claim_type, product_code, language, narrative_anon, _ingest_ts",
        "narrative_anon",
    ),
    "devices.device": (
        "device_id, imei_sha256, serial_sha256, make, model, model_year, device_category, "
        "oem_id, purchase_country, purchase_date, purchase_price, current_replacement_cost, "
        "oem_warranty_end_date, condition_grade, device_status, last_diagnostic_ts",
        "devices",
    ),
    "partners.partner_account": (
        "partner_id, partner_name, partner_type, parent_partner_id, region, country, "
        "go_live_date, take_rate_pct, revenue_share_struct, slas_struct, account_manager_id, "
        "solutions_engineer_id, contract_doc_id, status, last_qbr_date",
        "partners",
    ),
    "kb.article": (
        "article_id, title, body_md, language, category, applies_to_products, applies_to_countries, "
        "version, updated_at",
        "kb_articles",
    ),
    "kb.article_chunk": (
        "chunk_id, article_id, language, section_path, text, token_count, _ingest_ts",
        "kb_chunks",
    ),
}


def write_to_uc(args: Args, data: dict[str, list[tuple]], ws: WorkspaceClient | None = None) -> None:
    from databricks.sdk.service.sql import StatementParameterListItem

    # Reuse a caller-supplied client (e.g. scripts/bootstrap.py running under a
    # CLI profile) instead of constructing a host/token one — OAuth profiles
    # have no static token to pass.
    ws = ws or WorkspaceClient(host=args.workspace_host, token=args.token)
    for tbl, (cols, key) in _DDL_INSERT_BATCHES.items():
        rows = data[key]
        if not rows:
            continue
        n_cols = len(cols.split(","))
        # Batch inserts (200 rows per statement). databricks-sdk 0.40 requires
        # NAMED placeholders (`:p0, :p1, ...`) and `StatementParameterListItem`
        # instances — `?` + raw dicts crash with `'dict' object has no attribute 'as_dict'`.
        batch = 200
        for i in range(0, len(rows), batch):
            slice_ = rows[i:i + batch]
            flat = [v for row in slice_ for v in row]
            placeholder_names = [f"p{j}" for j in range(len(flat))]
            values_clause = ",".join(
                "(" + ",".join(f":{placeholder_names[r * n_cols + c]}" for c in range(n_cols)) + ")"
                for r in range(len(slice_))
            )
            stmt = (
                f"INSERT INTO {args.catalog_lake}.{tbl} ({cols}) VALUES {values_clause}"
            )
            params = [
                StatementParameterListItem(
                    name=name,
                    value=None if v is None else str(v),
                )
                for name, v in zip(placeholder_names, flat)
            ]
            ws.statement_execution.execute_statement(
                statement=stmt, warehouse_id=args.warehouse_id, parameters=params,
                wait_timeout="30s",
            )
        print(f"  wrote {len(rows)} rows -> {args.catalog_lake}.{tbl}")


def main() -> None:
    args = parse_args()
    print(f"Generating {args.rows} rows (seed={args.seed}, langs={args.languages})…")
    data = gen(args)
    print({k: len(v) for k, v in data.items()})
    write_to_uc(args, data)
    print("Seeding complete.")


if __name__ == "__main__":
    main()
