from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+pysqlite:///./portfolio.db"
    default_currency: str = "INR"
    max_position_weight: float = 0.15
    trim_position_weight: float = 0.20
    preferred_hold_days: int = 365
    loss_review_threshold: float = -0.15
    profit_review_threshold: float = 0.30
    buy_more_min_financial_score: int = 70
    upstox_analytics_token: str | None = None
    # Backward-compatible alias for the token name used by the initial local setup.
    upstox_api_key: str | None = None
    upstox_api_base_url: str = "https://api.upstox.com"
    analysis_schedule_hour: int = 6
    analysis_schedule_minute: int = 0
    analysis_schedule_days: str = "tue-sat"
    analysis_schedule_timezone: str = "Asia/Kolkata"
    analysis_run_on_startup: bool = True
    fundamentals_batch_size: int = 10
    screening_batch_size: int = 10
    universe_schedule_months: str = "4,10"
    investor_disclosure_lag_days: int = 21
    disclosure_auto_ingest_enabled: bool = True
    disclosure_batch_size: int = 10
    disclosure_request_interval_seconds: float = 1.0
    disclosure_cache_hours: int = 24
    disclosure_fuzzy_match_threshold: float = 0.84
    # Below this, a near-miss alias match is still recorded (queryable trail) but does not
    # raise an unread notification — every shareholding filing produces surname-pattern
    # near-zero-confidence "matches" that are routine noise, not a reviewable signal.
    disclosure_near_miss_notify_floor: float = 0.60
    disclosure_llm_provider: str = "auto"
    disclosure_llm_max_chars: int = 120000
    nse_disclosure_api_url: str = "https://www.nseindia.com/api/corporate-share-holdings-master"
    bse_disclosure_api_url: str = "https://api.bseindia.com/BseIndiaAPI/api/Corp_Shareholding_ng/w"
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemma-4-26b-a4b-it:free"
    document_max_bytes: int = 25000000
    document_llm_max_chars: int = 90000
    document_prompt_version: str = "grounded-analysis-v1"
    ipo_discovery_enabled: bool = True
    notification_webhook_url: str | None = None
    automation_alerts_enabled: bool = True
    analysis_start_grace_minutes: int = 15
    analysis_stall_minutes: int = 90
    analysis_catchup_max_hours: int = 48
    analysis_retry_delays_minutes: str = "30,60"
    scheduler_heartbeat_seconds: int = 60

    @property
    def upstox_token(self) -> str | None:
        return self.upstox_analytics_token or self.upstox_api_key

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
