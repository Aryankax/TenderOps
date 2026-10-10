from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    # Groq on_demand tier caps tokens-per-minute (TPM). max_tokens counts
    # against TPM, so this must stay well below the tier limit (8000 TPM
    # raised 413 "Request too large" when we used 8192).
    GROQ_MAX_TOKENS: int = 3000
    # Reasoning effort for gpt-oss models ("low", "medium", "high").
    GROQ_REASONING_EFFORT: str = "medium"
    # Per-chunk input cap so prompt + completion stay inside the TPM budget.
    TENDER_ANALYST_MAX_CHUNK_CHARS: int = 6000
    NEATLOGS_API_KEY: Optional[str] = None
    MAX_UPLOAD_SIZE_MB: int = 10
    TENDER_ANALYST_MAX_RETRIES: int = 2
    TENDER_ANALYST_CHUNK_SIZE: int = 1

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
