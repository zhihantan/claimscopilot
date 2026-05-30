"""Runtime configuration for ClaimsCopilot.

Every name (catalog, schema, table, endpoint, secret) is read from environment
variables. Databricks Apps injects these via `app.yaml`. Local dev uses a
`.envrc` (NEVER commit one). Nothing is hardcoded.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CC_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # Feature gating
    enabled: bool = Field(default=True, alias="CC_ENABLED")
    agent_version: str = Field(default="v0.3.3", alias="CC_AGENT_VERSION")
    app_env: Literal["dev", "stage", "prod"] = Field(default="dev", alias="CC_APP_ENV")
    region: Literal["EMEA", "APAC", "AMER"] = Field(default="EMEA", alias="CC_REGION")

    # Databricks workspace
    databricks_host: str = Field(..., alias="DATABRICKS_HOST")
    databricks_warehouse_id: str = Field(..., alias="CC_WAREHOUSE_ID")

    # Catalogs / schemas — generic defaults; set per workspace via env (app.yaml)
    # or scripts/init.py. Nothing here is workspace-specific.
    catalog_lake: str = Field(default="main", alias="CC_CATALOG_LAKE")
    catalog_ai: str = Field(default="main", alias="CC_CATALOG_AI")
    schema_tools: str = Field(default="tools", alias="CC_SCHEMA_TOOLS")
    schema_app: str = Field(default="app", alias="CC_SCHEMA_APP")

    # Vector Search. Index names default to None and are derived from catalog_ai
    # (see _derive_vs_index_names) so changing the catalog flows everywhere; the
    # CC_VS_IDX_* env vars still override if your indexes live elsewhere.
    vs_endpoint: str = Field(default="claimscopilot_vs", alias="CC_VS_ENDPOINT")
    vs_index_policy_wordings: Optional[str] = Field(default=None, alias="CC_VS_IDX_POLICY")
    vs_index_adjuster_kb: Optional[str] = Field(default=None, alias="CC_VS_IDX_KB")
    vs_index_claim_narratives: Optional[str] = Field(default=None, alias="CC_VS_IDX_CLAIMS")

    # Foundation Model APIs
    chat_endpoint_primary: str = Field(
        default="databricks-claude-opus-4-8", alias="CC_CHAT_PRIMARY"
    )
    chat_endpoint_fallback_1: str = Field(
        default="databricks-claude-sonnet-4-6", alias="CC_CHAT_FB1"
    )
    chat_endpoint_fallback_2: str = Field(
        default="databricks-meta-llama-3-3-70b-instruct", alias="CC_CHAT_FB2"
    )
    embed_endpoint_en: str = Field(
        default="databricks-gte-large-en", alias="CC_EMBED_EN"
    )
    embed_endpoint_multilingual: str = Field(
        default="databricks-qwen3-embedding-0-6b", alias="CC_EMBED_ML"
    )

    # AI Gateway endpoints (these proxy the FMAPI endpoints with guardrails)
    gateway_chat: str = Field(
        default="claimscopilot-chat-gateway", alias="CC_GATEWAY_CHAT"
    )
    gateway_embed: str = Field(
        default="claimscopilot-embed-gateway", alias="CC_GATEWAY_EMBED"
    )
    # When true, route chat through `gateway_chat` first (raw FMAPI endpoints
    # remain the fallback). Enable after provisioning AI Gateway guardrails —
    # see scripts/setup_ai_gateway.py.
    use_gateway: bool = Field(default=False, alias="CC_USE_GATEWAY")

    # Genie (optional NL->SQL analytical tool). Leave the space id unset to
    # disable the tool — it won't be offered to the planner.
    genie_space_id: Optional[str] = Field(default=None, alias="CC_GENIE_SPACE_ID")
    genie_timeout_s: float = Field(default=30.0, alias="CC_GENIE_TIMEOUT")

    # MLflow
    mlflow_experiment: str = Field(
        default="/Shared/claimscopilot", alias="CC_MLFLOW_EXPERIMENT"
    )

    # Agent graph state persistence:
    #   "memory"   — in-process MemorySaver (survives reconnects within a worker,
    #                NOT a container restart). Default.
    #   "none"     — no checkpointing.
    #   "lakebase" — durable AsyncPostgresSaver backed by a Lakebase Postgres
    #                instance (survives container restarts; cross-worker). Opened
    #                asynchronously in ClaimsCopilotAgent.aopen(); see
    #                backend/agent/lakebase.py.
    checkpointer: Literal["memory", "none", "lakebase"] = Field(
        default="memory", alias="CC_CHECKPOINTER"
    )
    # Max psycopg pool size for the lakebase checkpointer.
    lakebase_pool_max_size: int = Field(default=10, alias="CC_LAKEBASE_POOL_MAX")

    # Loop budgets
    max_tool_calls_per_turn: int = Field(default=8, alias="CC_MAX_TOOLS_PER_TURN")
    max_reflect_cycles: int = Field(default=3, alias="CC_MAX_REFLECT")
    turn_soft_timeout_s: float = Field(default=15.0, alias="CC_TURN_SOFT_TIMEOUT")
    turn_hard_timeout_s: float = Field(default=25.0, alias="CC_TURN_HARD_TIMEOUT")
    tool_timeout_s: float = Field(default=5.0, alias="CC_TOOL_TIMEOUT")
    vs_timeout_s: float = Field(default=10.0, alias="CC_VS_TIMEOUT")
    translate_timeout_s: float = Field(default=12.0, alias="CC_TRANSLATE_TIMEOUT")

    # Token budgets
    synth_input_token_cap: int = Field(default=4500, alias="CC_SYNTH_INPUT_CAP")

    # Languages
    allowed_languages: list[str] = Field(
        default_factory=lambda: ["en", "es", "ja"],
        alias="CC_ALLOWED_LANGS",
    )

    # Pricing (kept in env so we don't redeploy when list price changes)
    price_in_usd_per_mtok_primary: float = Field(default=3.00, alias="CC_PRICE_IN_PRIMARY")
    price_out_usd_per_mtok_primary: float = Field(default=15.00, alias="CC_PRICE_OUT_PRIMARY")

    # Safety
    fraud_score_siu_threshold: float = Field(default=0.7, alias="CC_FRAUD_THRESHOLD")
    materiality_threshold_usd: float = Field(default=5000.0, alias="CC_MATERIALITY")

    # Secrets scope (no secrets in env directly)
    secret_scope: str = Field(default="claimscopilot", alias="CC_SECRET_SCOPE")

    # Honeypot canary; rotate per deployment via app.yaml
    system_canary: str = Field(..., alias="CC_SYSTEM_CANARY")

    @model_validator(mode="after")
    def _derive_vs_index_names(self) -> "Settings":
        """Default each VS index to <catalog_ai>.indexes.<name> when not set via
        CC_VS_IDX_*, so a single catalog change propagates to all three."""
        if self.vs_index_policy_wordings is None:
            self.vs_index_policy_wordings = f"{self.catalog_ai}.indexes.policy_wordings"
        if self.vs_index_adjuster_kb is None:
            self.vs_index_adjuster_kb = f"{self.catalog_ai}.indexes.adjuster_kb"
        if self.vs_index_claim_narratives is None:
            self.vs_index_claim_narratives = f"{self.catalog_ai}.indexes.claim_narratives"
        return self

    # Convenience accessors --------------------------------------------------
    @property
    def session_table(self) -> str:
        return f"{self.catalog_ai}.{self.schema_app}.session"

    @property
    def message_table(self) -> str:
        return f"{self.catalog_ai}.{self.schema_app}.message"

    @property
    def tool_call_table(self) -> str:
        return f"{self.catalog_ai}.{self.schema_app}.tool_call"

    @property
    def feedback_table(self) -> str:
        return f"{self.catalog_ai}.{self.schema_app}.feedback"

    @property
    def decision_log_table(self) -> str:
        return f"{self.catalog_ai}.{self.schema_app}.decision_log"

    @property
    def feature_flag_table(self) -> str:
        return f"{self.catalog_ai}.{self.schema_app}.feature_flag"

    def tool_function(self, name: str) -> str:
        return f"{self.catalog_lake}.{self.schema_tools}.{name}"

    def chat_endpoint_chain(self) -> list[str]:
        """Chat endpoint fallback order. With `use_gateway` on, the AI Gateway
        endpoint is tried first and the raw FMAPI endpoints are the fallback."""
        chain = [self.chat_endpoint_primary, self.chat_endpoint_fallback_1,
                 self.chat_endpoint_fallback_2]
        if self.use_gateway and self.gateway_chat:
            return [self.gateway_chat, *chain]
        return chain


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
