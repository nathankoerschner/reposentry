from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Worker settings loaded from environment variables."""

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/reposentry"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Scanner
    clone_base_dir: str = "/tmp/reposentry-clones"
    max_file_retries: int = 1
    max_concurrent_files: int = 3
    max_stage2_iterations: int = 2
    llm_max_tokens: int = 2000

    # General
    environment: str = "development"
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
