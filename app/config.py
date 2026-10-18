import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    DATABASE_URL: Optional[str] = None
    NEON_DATABASE_URL: Optional[str] = None
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    NEYNAR_API_KEY: Optional[str] = None
    CRON_SECRET: Optional[str] = None
    MARKET_CLOSE_HOUR: int = 16
    MARKET_CLOSE_MINUTE: int = 30
    ROMA_FRAMEWORK_MODULE: Optional[str] = None
    ENVIRONMENT: Optional[str] = None
    VERCEL: Optional[str] = None

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
    def is_vercel(self) -> bool:
        return bool(self.ENVIRONMENT == 'vercel' or self.VERCEL or os.getenv('VERCEL'))

settings = Settings()
