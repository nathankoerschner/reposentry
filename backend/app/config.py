from pydantic_settings import BaseSettings


DEFAULT_CORS_ALLOWED_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/reposentry"

    # Clerk
    clerk_secret_key: str = ""
    clerk_publishable_key: str = ""
    clerk_jwks_url: str = ""

    # Google Cloud Pub/Sub
    gcp_project_id: str = ""
    pubsub_topic_id: str = "scan-jobs"

    # Worker dispatch
    worker_url: str = "http://localhost:8001"

    # HTTP / CORS
    cors_allowed_origins: str = DEFAULT_CORS_ALLOWED_ORIGINS
    cors_allowed_origin_regex: str = ""

    # General
    environment: str = "development"
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
