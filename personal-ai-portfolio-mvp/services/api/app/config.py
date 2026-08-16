from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+pysqlite:///./portfolio.db"
    default_currency: str = "INR"
    max_position_weight: float = 0.15
    trim_position_weight: float = 0.20
    preferred_hold_days: int = 365
    loss_review_threshold: float = -0.15
    profit_review_threshold: float = 0.30
    upstox_analytics_token: str | None = None
    # Backward-compatible alias for the token name used by the initial local setup.
    upstox_api_key: str | None = None
    upstox_api_base_url: str = "https://api.upstox.com"

    @property
    def upstox_token(self) -> str | None:
        return self.upstox_analytics_token or self.upstox_api_key

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
