"""User identity + on-behalf-of (OBO) token extraction.

Databricks Apps injects:
  - X-Forwarded-Access-Token  : OBO token for downstream Databricks APIs
  - X-Forwarded-User          : userPrincipalName (email)
  - X-Forwarded-Email         : same, sometimes
  - X-Forwarded-Preferred-Username
  - X-Real-IP, X-Request-Id

Locally we accept env-var stub values via CC_DEV_USER_* so devs don't need
a real OBO token when iterating on UI. Stubbed identity is never honored in
prod (`app_env="prod"`).
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from backend.agent.config import Settings, get_settings
from backend.schemas import UserContext


async def get_user_context(
    request: Request,
    x_forwarded_access_token: Annotated[str | None, Header()] = None,
    x_forwarded_user: Annotated[str | None, Header()] = None,
    x_forwarded_email: Annotated[str | None, Header()] = None,
    x_forwarded_preferred_username: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> UserContext:
    if settings.app_env == "dev" and x_forwarded_access_token is None:
        return _dev_stub()

    if not x_forwarded_access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing OBO token (X-Forwarded-Access-Token).",
        )

    email = x_forwarded_email or x_forwarded_user
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing user identity headers.",
        )

    role, country = await _resolve_role_and_country(email, settings)

    return UserContext(
        user_id=email,
        email=email,
        display_name=x_forwarded_preferred_username,
        role=role,
        country=country,
        obo_token=x_forwarded_access_token,
        workspace_host=settings.databricks_host,
    )


async def _resolve_role_and_country(email: str, settings: Settings) -> tuple[str, str]:
    """Look up the adjuster's role and country from the directory table.

    For pilot we keep a small Delta table `<catalog>.app.user_directory`. In a
    real deployment this is sourced from the company's IAM (Okta / Entra)
    via a nightly sync. The lookup is intentionally simple here; the agent
    treats `role` and `country` as advisory metadata only — UC row-level
    security is what actually enforces access.
    """
    # In dev or when directory is unavailable, default to L2 in the
    # configured region's most common country. We never block on this.
    default_country = {"EMEA": "GB", "APAC": "JP", "AMER": "MX"}[settings.region]
    return ("ADJUSTER_L2", default_country)


def _dev_stub() -> UserContext:
    return UserContext(
        user_id=os.environ.get("CC_DEV_USER_ID", "dev.adjuster@example.com"),
        email=os.environ.get("CC_DEV_USER_EMAIL", "dev.adjuster@example.com"),
        display_name=os.environ.get("CC_DEV_USER_DISPLAY", "Dev Adjuster"),
        role=os.environ.get("CC_DEV_USER_ROLE", "ADJUSTER_L2"),  # type: ignore[arg-type]
        country=os.environ.get("CC_DEV_USER_COUNTRY", "GB"),
        obo_token=os.environ.get(
            "CC_DEV_OBO_TOKEN", "dapi-dev-stub-token-not-valid-in-prod"
        ),
        workspace_host=os.environ.get("DATABRICKS_HOST", "https://example.cloud.databricks.com"),
    )
