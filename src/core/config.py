from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings managed via environment variables and .env file.
    """
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenAI / LLM Configuration
    openai_api_key: str = Field(..., description="OpenAI API Key")
    # Model and Timeout are now per-task configuration
    
    # Retry Configuration
    max_retries: int = Field(3, description="Maximum number of retries for LLM calls")

    # Logging
    log_level: str = Field("INFO", description="Logging level")
    
    # Paths
    project_root: Path = Field(default_factory=lambda: Path(__file__).parent.parent.parent)

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

settings = Settings()
