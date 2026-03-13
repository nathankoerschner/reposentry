from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/zeropath"

    # Clerk
    clerk_secret_key: str = ""
    clerk_publishable_key: str = ""
    clerk_jwks_url: str = ""

    # Google Cloud Pub/Sub
    gcp_project_id: str = ""
    pubsub_topic_id: str = "scan-jobs"

    # HTTP / CORS
    cors_allowed_origins: str = "http://localhost:5173"
    cors_allowed_origin_regex: str = ""

    # General
    environment: str = "development"
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
