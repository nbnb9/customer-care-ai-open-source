import re
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",  # 相对 CWD，即 apps/api/.env
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    allow_demo_identity: bool = False
    allow_destructive_seed: bool = False
    cors_allowed_origins: str = "http://localhost:3000"

    database_url: str  # 必填：缺失则启动即失败（fail-fast）
    redis_url: str = "redis://127.0.0.1:16379/0"

    # 对象存储暂未使用，先声明为可选
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "customercare"

    # 知识导入（步骤 28）：版本化的本地分块参数；生产环境应由配置中心管理。
    knowledge_chunk_max_chars: int = 480
    knowledge_chunk_overlap_chars: int = 60

    # 步骤 32：先用数据库锁实现可验证的客户级起步限流；生产阶段会替换为
    # 分布式限流与监控告警，但不得把保护完全留给浏览器按钮。
    conversation_message_rate_limit: int = 20
    conversation_message_rate_window_seconds: int = 60

    # The schema still applies field-level limits.  This boundary rejects a
    # pathological payload before JSON parsing can consume unbounded memory.
    # Four MiB leaves room for the current one-million-character knowledge
    # import contract, including UTF-8 and JSON overhead.
    max_request_body_bytes: int = 4 * 1024 * 1024

    # 步骤 39：本地可观测性只保存脱敏关联元数据。部署版本必须由发布
    # 流程明确注入，不能从浏览器、模型或请求内容推断。
    deployment_version: str = "local-dev"
    observability_trace_retention_hours: int = 72

    # 步骤 33：全局主开关默认关闭；每个租户还必须在数据库中显式配置
    # canary/kill-switch。主开关用于发布面控制，租户开关可即时关闭。
    r0_self_service_master_enabled: bool = False
    r0_self_service_product: str = "demo-support"
    r0_self_service_language: str = "zh-CN"

    # LLM 网关（步骤 23）
    llm_provider: str = "fake"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""

    # 步骤 41.5：真实 OMS 沙箱读取仍默认关闭。实际映射和凭据只从
    # 本机/CI 文件读取，避免把客户 GID 或 client secret 放进浏览器和 Git。
    customer_tools_provider: str = "mock"
    shopify_api_version: str = "2026-07"
    shopify_client_id_file: str = ""
    shopify_client_secret_file: str = ""
    shopify_sandbox_bindings_file: str = ""
    shopify_timeout_seconds: float = 8.0
    shopify_external_writes_enabled: bool = False

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.cors_allowed_origins.split(",")
        ]

    @model_validator(mode="after")
    def validate_cors_origins(self) -> "Settings":
        origins = self.allowed_origins
        if not origins or any(not origin for origin in origins):
            raise ValueError("CORS_ALLOWED_ORIGINS must contain at least one origin")
        for origin in origins:
            parsed = urlsplit(origin)
            try:
                _ = parsed.port
            except ValueError as exc:
                raise ValueError(f"Invalid CORS origin: {origin}") from exc
            if (
                origin == "*"
                or any(char.isspace() for char in origin)
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"Invalid CORS origin: {origin}")
            if self.app_env not in {"development", "test"} and parsed.scheme != "https":
                raise ValueError("Non-development CORS origins must use https")
        return self

    @model_validator(mode="after")
    def validate_knowledge_chunker(self) -> "Settings":
        if self.knowledge_chunk_max_chars < 1:
            raise ValueError("KNOWLEDGE_CHUNK_MAX_CHARS must be positive")
        if not 0 <= self.knowledge_chunk_overlap_chars < self.knowledge_chunk_max_chars:
            raise ValueError(
                "KNOWLEDGE_CHUNK_OVERLAP_CHARS must be within chunk max chars"
            )
        return self

    @model_validator(mode="after")
    def validate_conversation_rate_limit(self) -> "Settings":
        if self.conversation_message_rate_limit < 1:
            raise ValueError("CONVERSATION_MESSAGE_RATE_LIMIT must be positive")
        if not 1 <= self.conversation_message_rate_window_seconds <= 3_600:
            raise ValueError(
                "CONVERSATION_MESSAGE_RATE_WINDOW_SECONDS must be between 1 and 3600"
            )
        return self

    @model_validator(mode="after")
    def validate_request_body_limit(self) -> "Settings":
        if not 1_024 <= self.max_request_body_bytes <= 32 * 1024 * 1024:
            raise ValueError("MAX_REQUEST_BODY_BYTES must be between 1024 and 33554432")
        return self

    @model_validator(mode="after")
    def validate_observability_settings(self) -> "Settings":
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", self.deployment_version
        ):
            raise ValueError(
                "DEPLOYMENT_VERSION must be a safe identifier up to 128 characters"
            )
        if not 1 <= self.observability_trace_retention_hours <= 720:
            raise ValueError(
                "OBSERVABILITY_TRACE_RETENTION_HOURS must be between 1 and 720"
            )
        return self

    @model_validator(mode="after")
    def validate_r0_self_service_scope(self) -> "Settings":
        if not self.r0_self_service_product.strip():
            raise ValueError("R0_SELF_SERVICE_PRODUCT must not be empty")
        if not self.r0_self_service_language.strip():
            raise ValueError("R0_SELF_SERVICE_LANGUAGE must not be empty")
        return self

    @model_validator(mode="after")
    def validate_shopify_sandbox(self) -> "Settings":
        if self.customer_tools_provider not in {"mock", "shopify_sandbox"}:
            raise ValueError("CUSTOMER_TOOLS_PROVIDER must be mock or shopify_sandbox")
        if self.shopify_api_version != "2026-07":
            raise ValueError("SHOPIFY_API_VERSION must be pinned to 2026-07")
        if not 1.0 <= self.shopify_timeout_seconds <= 30.0:
            raise ValueError("SHOPIFY_TIMEOUT_SECONDS must be between 1 and 30")
        if self.shopify_external_writes_enabled:
            raise ValueError(
                "SHOPIFY_EXTERNAL_WRITES_ENABLED is not supported by "
                "the read-only adapter"
            )
        if self.customer_tools_provider == "shopify_sandbox":
            if self.app_env not in {"development", "test"}:
                raise ValueError(
                    "shopify_sandbox is only allowed in development or test"
                )
            required_files = {
                "SHOPIFY_CLIENT_ID_FILE": self.shopify_client_id_file,
                "SHOPIFY_CLIENT_SECRET_FILE": self.shopify_client_secret_file,
                "SHOPIFY_SANDBOX_BINDINGS_FILE": self.shopify_sandbox_bindings_file,
            }
            missing = [name for name, value in required_files.items() if not value]
            if missing:
                names = ", ".join(missing)
                raise ValueError(f"Missing Shopify sandbox file settings: {names}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated by BaseSettings sources
