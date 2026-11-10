"""Application settings, loaded once from environment / backend/.env.

Why pydantic-settings: a missing or malformed setting fails loudly at startup,
not in the middle of a demo when the first PayPal call happens.
"""

from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The REST API hosts. sandbox.paypal.com / www.paypal.com are the websites,
# which answer API calls with redirects instead of errors.
_PAYPAL_API_HOSTS = {"api-m.sandbox.paypal.com", "api-m.paypal.com"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    paypal_client_id: str
    # SecretStr hides the value in logs and reprs: print(settings) won't leak it.
    paypal_client_secret: SecretStr
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"

    anthropic_api_key: SecretStr | None = None
    agent_model: str = "claude-opus-5-5"
    agent_effort: str = "medium"  # low | medium | high | xhigh | max
    agent_max_steps: int = 12
    # Where PayPal sends the owner back to (this API) ...
    public_base_url: str = "http://localhost:8000"
    # ... and where the API then redirects them (the React dashboard).
    frontend_url: str = "http://localhost:5173"
    database_path: str = "spendguard.db"
    # The sandbox business account that sells the demo catalog's items.
    demo_merchant_email: str

    @field_validator("paypal_base_url")
    @classmethod
    def _paypal_api_host(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.hostname not in _PAYPAL_API_HOSTS:
            raise ValueError(
                f"{value!r} is not a PayPal REST API URL; "
                "use https://api-m.sandbox.paypal.com for sandbox"
            )
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
