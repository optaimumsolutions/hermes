from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str
    lead_database_url: str = ""  # Upstream lead source from another agent
    hunter_api_key: str = ""
    instantly_api_key: str = ""
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    slack_bot_token: str = ""
    slack_channel_id: str = ""
    slack_app_token: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    neverbounce_api_key: str = ""
    # Traffic monitor
    ga4_property_optaimum: str = ""
    ga4_property_catchflow: str = ""
    cloudflare_api_token: str = ""
    cloudflare_zone_optaimum: str = ""
    cloudflare_zone_catchflow: str = ""
    scout_batch_size: int = 25
    sender_daily_limit: int = 50
    lead_score_threshold: int = 6

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
