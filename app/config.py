import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    DATABASE_URL: Optional[str] = None
    NEON_DATABASE_URL: Optional[str] = None
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    CRON_SECRET: Optional[str] = None
    MARKET_CLOSE_HOUR: int = 16
    MARKET_CLOSE_MINUTE: int = 30

    # OpenRouter AI Intelligence Engine
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_MODEL: str = "meta-llama/llama-3.3-70b-instruct:free"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    ENVIRONMENT: Optional[str] = None
    VERCEL: Optional[str] = None
    CLOUDFLARE: Optional[str] = None

    # Session & Security
    SESSION_COOKIE_SECURE: Optional[bool] = None
    MAX_LOGIN_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_SECONDS: int = 60

    # Memcached Centralized Cache (e.g. 127.0.0.1:11211)
    MEMCACHED_SERVERS: Optional[str] = None

    # Support Email Notifications via Standard SMTP (e.g. Gmail App Password)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_USE_TLS: bool = True
    DEV_NOTIFICATION_EMAIL: Optional[str] = None

    @property
    def effective_database_url(self) -> str:
        return (
            self.NEON_DATABASE_URL
            or self.DATABASE_URL
            or os.getenv('NEON_DATABASE_URL')
            or os.getenv('DATABASE_URL')
            or 'postgresql://localhost/stocks'
        )

    @property
    def is_serverless(self) -> bool:
        """True when running in a serverless environment (Vercel, Cloudflare, etc.) where cron triggers are externally managed."""
        return bool(
            self.ENVIRONMENT in ('vercel', 'cloudflare', 'serverless')
            or self.VERCEL
            or os.getenv('VERCEL')
            or self.CLOUDFLARE
            or os.getenv('CLOUDFLARE')
        )

    @property
    def is_vercel(self) -> bool:
        return self.is_serverless

settings = Settings()
