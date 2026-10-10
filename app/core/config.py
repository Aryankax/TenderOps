from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    # Primary provider: LiteLLM OpenAI-compatible gateway (Groq is fallback).
    LITELLM_API_KEY: Optional[str] = None
    LITELLM_BASE_URL: str = "https://litellm.vision.aivar.app/v1"
    # Gateway model id (e.g. claude-haiku, claude-sonnet-4.6, nova-pro).
    LITELLM_MODEL: str = "claude-haiku"
    # Groq on_demand tier caps tokens-per-minute (TPM). max_tokens counts
    # against TPM, so this must stay well below the tier limit (8000 TPM
    # raised 413 "Request too large" when we used 8192). Sized for
    # reasoning-token burn (reasoning_effort=medium consumes completion
    # budget before the answer): too small a budget truncates the JSON and
    # surfaces as json_validate_failed with empty failed_generation.
    GROQ_MAX_TOKENS: int = 4000
    # Reasoning effort for gpt-oss models ("low", "medium", "high").
    GROQ_REASONING_EFFORT: str = "medium"
    # Per-chunk input cap so prompt + completion stay inside the TPM budget.
    TENDER_ANALYST_MAX_CHUNK_CHARS: int = 6000
    NEATLOGS_API_KEY: Optional[str] = None
    MAX_UPLOAD_SIZE_MB: int = 10
    TENDER_ANALYST_MAX_RETRIES: int = 2
    TENDER_ANALYST_CHUNK_SIZE: int = 1
    # Evidence agent (P2): local Chroma index, no API budget on retrieval.
    EVIDENCE_CHROMA_DIR: str = "./data/evidence_chroma"
    EVIDENCE_TOP_K: int = 3
    EVIDENCE_MIN_SCORE: float = 0.15
    EVIDENCE_MAP_BATCH_SIZE: int = 6

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
