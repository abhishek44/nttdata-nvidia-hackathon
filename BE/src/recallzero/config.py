from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    nhtsa_base_url: str = "https://api.nhtsa.gov"
    nvidia_nim_llm_url: str = "http://localhost:8000/v1"
    nvidia_nim_llm_model: str = ""
    nvidia_nim_embed_url: str = "http://localhost:8001/v1"
    nvidia_nim_embed_model: str = ""
    nvidia_api_key: str = ""
    recallzero_alert_threshold: float = 75.0
    recallzero_min_cluster_size: int = 4

    model_config = SettingsConfigDict(env_file=(".env", "BE/.env"), env_file_encoding="utf-8", extra="ignore")


settings = Settings()
