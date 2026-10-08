"""Infrastructure settings, loaded from environment variables (prefix ``PE_``) or a ``.env`` file.

Only deployment/infrastructure values (and the optional AI assistant key) live here. Business configuration
(organisation name, currency, business timezone, roles and permissions) is stored in MongoDB and managed through
the application.
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PE_", extra="ignore")

    mongo_uri: str = "mongodb://localhost:27017"
    db_name: str = "sales_order_promotions"
    mongo_app_name: str = "promotion-engine"
    mongo_max_pool_size: int = 50
    mongo_server_selection_timeout_ms: int = 5000

    jwt_secret: str = "change-this-secret-in-production-please-32b"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # Public URL of the React app (e.g. its ngrok URL). Added to the CORS origins; comma-separate several.
    # Read from PE_FRONTEND_URL or FRONTEND_URL so the public URL changes without touching the code.
    frontend_url: str = Field("http://localhost:5173", validation_alias=AliasChoices("PE_FRONTEND_URL", "FRONTEND_URL"))
    # Optional regex of extra allowed origins, e.g. ^https://[a-z0-9-]+\.ngrok-free\.app$ when the free ngrok URL
    # changes on every restart. Empty = only the explicit origins above.
    cors_origin_regex: str | None = None
    # Host headers the API answers to: "*" (any, the default) or a comma-separated list such as
    # "localhost,127.0.0.1,*.ngrok-free.app". Requests with any other Host get 400.
    trusted_hosts: str = "*"

    @property
    def allowed_origins(self) -> list[str]:
        """Explicit CORS origins: PE_CORS_ORIGINS plus FRONTEND_URL (no trailing slash, no duplicates)."""
        extra = [u.strip() for u in self.frontend_url.split(",")]
        return list(dict.fromkeys(o.strip().rstrip("/") for o in [*self.cors_origins, *extra] if o and o.strip()))

    @property
    def trusted_host_list(self) -> list[str]:
        return [h.strip() for h in self.trusted_hosts.split(",") if h.strip()] or ["*"]


settings = Settings()
