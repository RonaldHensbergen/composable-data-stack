import os

SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY", "")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.environ.get("SUPERSET_SESSION_COOKIE_SECURE", "false").lower() == "true"
PERMANENT_SESSION_LIFETIME = int(os.environ.get("SUPERSET_SESSION_LIFETIME", "1800"))
WELCOME_PAGE_LAST_TAB = "examples"

_oidc_issuer_url = os.environ.get("CDS_OIDC_ISSUER_URL", "").rstrip("/")
_oidc_client_id = os.environ.get("CDS_OIDC_CLIENT_ID", "")
_oidc_client_secret = os.environ.get("CDS_OIDC_CLIENT_SECRET", "")
_oidc_values = (_oidc_issuer_url, _oidc_client_id, _oidc_client_secret)

if any(_oidc_values) and not all(_oidc_values):
    raise RuntimeError(
        "Superset OIDC requires issuer URL, client ID, and client secret together."
    )

if all(_oidc_values):
    from flask_appbuilder.security.manager import AUTH_OAUTH

    AUTH_TYPE = AUTH_OAUTH
    AUTH_USER_REGISTRATION = True
    AUTH_USER_REGISTRATION_ROLE = "Gamma"
    OAUTH_PROVIDERS = [
        {
            "name": "keycloak",
            "icon": "fa-key",
            # "access_token" is an OAuth response field name, not a credential.
            "token_key": "access_token",  # nosec B105
            "remote_app": {
                "client_id": _oidc_client_id,
                "client_secret": _oidc_client_secret,
                "server_metadata_url": (
                    f"{_oidc_issuer_url}/.well-known/openid-configuration"
                ),
                "client_kwargs": {"scope": "openid email profile"},
            },
        }
    ]
