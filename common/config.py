"""Central settings for every service, loaded from the .env file.

Usage anywhere in the project:
    from common.config import settings
    print(settings.qdrant_url)
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM (any OpenAI-compatible provider: Gemini, Groq, Ollama, ...)
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: float = 180.0          # seconds per request
    llm_json_mode: bool = True          # ask the provider for strict JSON output
    llm_reasoning_effort: str = ""      # optional: "low" makes Gemini think less = faster

    # Postgres
    postgres_user: str = "fashion"
    postgres_password: str = "fashion"
    postgres_db: str = "fashion"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_http_port: int = 6333
    qdrant_grpc_port: int = 6334

    # Local models
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    hf_home: str = "./models_cache"
    hf_token: str = ""

    # Data
    data_dir: Path = Path("./data")

    # ---- Derived values ----
    @property
    def postgres_dsn(self) -> str:
        """Plain DSN for psycopg."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sqlalchemy_url(self) -> str:
        """URL for SQLAlchemy using the psycopg3 driver."""
        return self.postgres_dsn.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_http_port}"


settings = Settings()
