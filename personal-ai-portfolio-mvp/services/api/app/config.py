from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+pysqlite:///./portfolio.db"
    default_currency: str = "INR"
    max_position_weight: float = 0.15
    trim_position_weight: float = 0.20
    preferred_hold_days: int = 365
    loss_review_threshold: float = -0.15
    profit_review_threshold: float = 0.30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
