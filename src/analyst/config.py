"""Typed application settings, loaded from environment variables and `.env`.

One settings object, validated at startup, means a missing or malformed variable
fails loudly on boot instead of halfway through a user's request.

Note what is NOT here: the user's data sources. Those are registered at runtime
through the API and stored (encrypted) in the app database, because the agent is
meant to work with any database anyone plugs in.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: `MCP_URL=` in .env means "not set" (None), not the empty string.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # --- App database (ours) ---
    app_db_host: str = "localhost"
    app_db_port: int = 5433
    app_db_name: str = "agent_app"
    app_db_user: str
    # SecretStr hides the value in repr/logs: print(settings) shows '**********'.
    app_db_password: SecretStr

    # --- Secrets ---
    # Fernet key that encrypts data-source credentials at rest. Only the MCP server needs it.
    app_secret_key: SecretStr | None = None
    # HMAC key used to sign human approvals; shared by the API (signs) and MCP server (verifies).
    approval_signing_key: SecretStr | None = None
    # Bearer token the API presents to the MCP server.
    mcp_auth_token: SecretStr | None = None

    # --- MCP ---
    # Unset = run the MCP server in-process (handy for dev, tests and evals).
    mcp_url: str | None = None
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8001

    # --- Chat LLM (any OpenAI-compatible endpoint: OpenAI, vLLM, Ollama, LiteLLM, ...) ---
    openai_base_url: str | None = None  # None = api.openai.com
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    # How structured output is requested. 'json_schema' needs server-side guided decoding
    # (OpenAI, vLLM); use 'function_calling' or 'json_mode' if your server lacks it.
    llm_structured_method: Literal["json_schema", "function_calling", "json_mode"] = "json_schema"
    # Qwen3-style reasoning models: turn off <think> output (vLLM/SGLang chat_template_kwargs).
    llm_disable_thinking: bool = False

    # --- Embeddings (any OpenAI-compatible /v1/embeddings endpoint) ---
    embedding_base_url: str | None = None  # e.g. http://127.0.0.1:8008/v1 (self-hosted BGE-M3)
    embedding_api_key: SecretStr | None = None  # falls back to OPENAI_API_KEY for api.openai.com
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024  # must match the vector(...) size in the migrations (checked at startup)
    embedding_batch_size: int = 64  # max inputs per request your embedding server accepts

    # --- Agent limits (cost and safety) ---
    max_result_rows: int = 200
    query_timeout_ms: int = 10_000
    max_sql_attempts: int = 3
    max_tasks_per_plan: int = 5
    max_graph_steps: int = 12
    approval_row_threshold: int = 1_000_000  # EXPLAIN estimate above this needs human approval
    max_question_chars: int = 4_000

    # --- Documents ---
    max_upload_mb: int = 20
    exclude_flagged_chunks: bool = True
    # File-based sources (CSV/Parquet/DuckDB) may only be read from inside this directory.
    local_data_dir: Path = Path("data")

    # --- API ---
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    log_level: str = "INFO"

    # --- Demo data source (tests, evals, demos only) ---
    demo_db_host: str = "localhost"
    demo_db_port: int = 5434
    demo_db_ro_password: SecretStr | None = None
    demo_db_admin_password: SecretStr | None = None

    @property
    def app_db_conninfo(self) -> str:
        return make_conninfo(
            host=self.app_db_host,
            port=self.app_db_port,
            dbname=self.app_db_name,
            user=self.app_db_user,
            password=self.app_db_password.get_secret_value(),
        )

    def _demo_conninfo(self, user: str, password: SecretStr | None) -> str:
        if password is None:
            raise RuntimeError(f"password for demo user {user!r} is not set")
        return make_conninfo(
            host=self.demo_db_host,
            port=self.demo_db_port,
            dbname="demo_retail",
            user=user,
            password=password.get_secret_value(),
        )

    @property
    def demo_warehouse_conninfo(self) -> str:
        """Connection string a user would paste in when plugging in the demo warehouse."""
        return self._demo_conninfo("analyst_ro", self.demo_db_ro_password)

    @property
    def demo_warehouse_admin_conninfo(self) -> str:
        """Admin login for the demo DB. Used only by tests proving we stay read-only anyway."""
        return self._demo_conninfo("demo_admin", self.demo_db_admin_password)

    def require(self, name: str) -> str:
        """Return a secret/required setting or fail with a clear message."""
        value = getattr(self, name)
        if value is None:
            raise RuntimeError(f"{name.upper()} must be set (see .env.example)")
        return value.get_secret_value() if isinstance(value, SecretStr) else str(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
