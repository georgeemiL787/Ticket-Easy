from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_path: str = "data/team_c.sqlite3"
    dev_reviewer_id: str = "local-owner"
    session_secret: str = ""
    llm_primary: str = "ollama"
    llm_fallback: str = "none"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout: float = 240
    ollama_context: int = 32768
    ollama_think: bool = False
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = ""
    openrouter_api_key: str = ""
    openrouter_timeout: float = 120
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"
    groq_api_key: str = ""
    groq_api_key_2: str = ""
    groq_api_key_3: str = ""
    groq_timeout: float = 120
    groq_reasoning_effort: str = "low"
    max_upload_bytes: int = 2 * 1024 * 1024
    max_operations: int = 50
    max_model_chars: int = 100_000
    openapi_fetch_hosts: str = ""
    openapi_fetch_timeout: float = 15
    connectors_file: str = ""
    sandbox_hosts: str = ""
    allowed_project_directory: str = ""
    code_max_files: int = 200
    code_max_entries: int = 3000
    code_max_file_bytes: int = 128 * 1024
    code_max_total_bytes: int = 2 * 1024 * 1024
    code_max_snippets: int = 12
    code_max_context_chars: int = 8000
    code_exploration_steps: int = 2
    suggestion_count: int = 3
    suggestion_max: int = 5
    capability_index_chars: int = 16000

    @property
    def groq_api_keys(self):
        """Configured credentials in priority order; blanks and duplicate keys are skipped."""
        return list(dict.fromkeys(key.strip() for key in (self.groq_api_key, self.groq_api_key_2, self.groq_api_key_3) if key.strip()))

    @property
    def model_secrets(self):
        return (self.openrouter_api_key, *self.groq_api_keys, self.session_secret)

    @property
    def llm_chain(self):
        """LLM_PRIMARY, then LLM_FALLBACK: none, one provider, or a comma-separated list tried in order."""
        fallback = [p.strip() for p in self.llm_fallback.split(",")]
        return [self.llm_primary] + ([] if fallback == ["none"] else fallback)


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, details=None):
        self.code, self.message, self.status = code, message, status
        self.details = details or {}
        super().__init__(message)
