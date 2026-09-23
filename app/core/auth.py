"""
AUTHENTICATION BOUNDARY — provider-neutral, pending Pine Labs SSO/OIDC
========================================================================

Supersedes the Part 4 stub that used to live in app/core/security.py
(which always returned a fake `id=0` identity to every caller — that
file has been removed; nothing in the codebase should reference it).

WHY PROVIDER-NEUTRAL: the actual Pine Labs identity provider is not yet
specified — no issuer URL, client id, JWKS endpoint, or token format has
been confirmed (backend design Part 3 §15, still open). This module
therefore does NOT assume Azure AD, Microsoft Entra ID, Okta, Auth0,
Cognito, SAML, or any specific OAuth/OIDC flow. It defines the SHAPE of
authentication this backend needs — a bearer token in, a resolved
CurrentUser out — via the `AuthenticationProvider` interface, with
exactly one concrete implementation today: `UnconfiguredAuthenticationProvider`,
which rejects every request. Wiring in the real Pine Labs provider later
means adding ONE new AuthenticationProvider subclass and pointing
`get_auth_provider()` at it — nothing that depends on `get_current_user`
(routers, `require_roles`, the audit service's actor plumbing) needs to
change at all.

STATUS: authentication boundary implemented; production identity-
provider validation remains pending Pine Labs SSO/OIDC configuration.
Every protected request is rejected with a structured 401 today — there
is no path by which an unauthenticated request can silently become an
authenticated user (see UnconfiguredAuthenticationProvider below).

WHY A DB SESSION IS PART OF authenticate(): role/status are
APPLICATION-managed (the local `users` table), not something any
external identity provider would know about — per Part 3 §15, "role/
access should stay locally managed in this database regardless of IdP".
So authenticating a token is a two-step concern even once a real
provider exists: (1) verify the token proves who the caller claims to
be, (2) resolve that identity against the local `users` row for
role/status. This interface's signature already reflects that.
"""
from abc import ABC, abstractmethod
from typing import Optional

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import ForbiddenError, UnauthorizedError


class CurrentUser(BaseModel):
    """The authenticated application user — always resolved from a real
    row in the local `users` table by an AuthenticationProvider, never
    fabricated or defaulted."""

    id: int
    name: str
    email: str
    role: str
    status: str


class AuthenticationProvider(ABC):
    """Provider-neutral interface. A concrete real-provider
    implementation (Pine Labs' actual SSO/OIDC integration) is NOT
    implemented here — see this module's docstring for exactly what
    configuration is missing to build one safely."""

    @abstractmethod
    def authenticate(self, token: str, db: Session) -> CurrentUser:
        """Validates `token` and returns the resolved CurrentUser, or
        raises an app.core.exceptions.UnauthorizedError (never returns a
        default/fake identity)."""
        raise NotImplementedError


class UnconfiguredAuthenticationProvider(AuthenticationProvider):
    """The only AuthenticationProvider that exists today. Deliberately
    rejects EVERY token, regardless of its value or validity — there is
    no real provider configured to validate against yet, and this class
    must never be tempted into "just letting requests through" as a
    convenience. This is what guarantees no request can silently become
    authenticated in the absence of real provider configuration."""

    def authenticate(self, token: str, db: Session) -> CurrentUser:
        raise UnauthorizedError(
            "Authentication provider is not configured. Pine Labs SSO/OIDC "
            "integration is pending confirmation — see app/core/auth.py.",
            code="AUTH_PROVIDER_NOT_CONFIGURED",
        )


def get_auth_provider() -> AuthenticationProvider:
    """
    FastAPI dependency (override-able in tests — see
    tests/integration/test_auth.py — to verify get_current_user's own
    token-extraction plumbing works correctly when SOME provider does
    succeed, without needing a real identity provider).

    Returns UnconfiguredAuthenticationProvider whenever AUTH_ISSUER/
    AUTH_AUDIENCE aren't both set, which is always true today. A real
    provider (JWKS fetch + token verification against the actual Pine
    Labs issuer) would be constructed here once that configuration is
    confirmed — not implemented yet.
    """
    settings = get_settings()
    if not settings.AUTH_ISSUER or not settings.AUTH_AUDIENCE:
        return UnconfiguredAuthenticationProvider()
    # A real provider implementation goes here once Pine Labs' actual
    # issuer/audience/JWKS configuration is confirmed. Intentionally not
    # implemented — see this module's docstring.
    return UnconfiguredAuthenticationProvider()


# HTTPBearer with auto_error=False so a missing/malformed header is
# reported through OUR structured error envelope below, not FastAPI's
# default raw {"detail": "Not authenticated"} response. Using FastAPI's
# HTTPBearer SecurityBase (rather than a plain header-reading function)
# is what makes protected routes correctly show a bearer-token
# requirement in OpenAPI/Swagger — see app/api/v1/router.py.
_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description=(
        "Bearer token authentication. NOTE: the real Pine Labs identity "
        "provider is not yet configured — every request is currently "
        "rejected with 401 AUTH_PROVIDER_NOT_CONFIGURED regardless of the "
        "token supplied. This documents the intended shape (a bearer "
        "token), not a working Pine Labs JWT today."
    ),
)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
    db: Session = Depends(get_db),
    provider: AuthenticationProvider = Depends(get_auth_provider),
) -> CurrentUser:
    """
    The FastAPI dependency every protected route depends on (directly,
    or via a router's `dependencies=[Depends(get_current_user)]` — see
    app/api/v1/router.py). Never hardcodes or defaults an identity: a
    missing/malformed Authorization header, or a provider that rejects
    the token, both result in a structured 401 — there is no fallback.
    """
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError(
            "Missing or malformed authentication credentials. Expected an "
            "'Authorization: Bearer <token>' header.",
            code="AUTH_MISSING_CREDENTIALS",
        )
    # Token contents are never included in any error — see
    # UnconfiguredAuthenticationProvider / a future real provider's
    # error messages, which reference only the fact of failure.
    return provider.authenticate(credentials.credentials, db)


def require_roles(*allowed_roles: str):
    """
    Returns a FastAPI dependency requiring the authenticated user's role
    to be one of `allowed_roles` (from app.models.user.ROLE_VALUES —
    Admin / Support Lead / Support Agent / Auditor), raising
    ForbiddenError (403) otherwise.

    NOT applied to any route yet. This establishes the reusable
    mechanism only — which roles should be allowed on which endpoint is
    a product decision that hasn't been made (Pine Labs' actual
    role/permission requirements per endpoint are unconfirmed). Intended
    future usage once those requirements exist:

        @router.delete(
            "/{userId}",
            dependencies=[Depends(require_roles("Admin"))],
        )
    """

    def _dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise ForbiddenError(
                f"Role {current_user.role!r} is not permitted to perform this action.",
                code="INSUFFICIENT_ROLE",
            )
        return current_user

    return _dependency
