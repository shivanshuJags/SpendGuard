"""Application settings, loaded once from environment / backend/.env.

Why pydantic-settings: a missing or malformed setting fails loudly at startup,
not in the middle of a demo when the first PayPal call happens.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    paypal_client_id: str
    # SecretStr hides the value in logs and reprs: print(settings) won't leak it.
    paypal_client_secret: SecretStr
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"

    anthropic_api_key: SecretStr | None = None
    public_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
