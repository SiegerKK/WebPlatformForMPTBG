from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://user:password@localhost/mptbg"
    REDIS_URL: str = "redis://localhost:6379"
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # LLM Game Master (OpenAI-compatible)
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"

    # World ticker
    TICK_INTERVAL_SECONDS: int = 3600  # 1 real-time hour = 1 game hour
    AUTO_TICK_ENABLED: bool = False    # set True in production; off by default for tests

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
