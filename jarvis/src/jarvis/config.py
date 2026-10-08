"""Configuration loading and validation for JARVIS.

All YAML config files are validated with pydantic models.
Supports ${VAR} interpolation from environment variables and .env files.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Env interpolation
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")


def _interpolate_env(value: str) -> str:
    """Replace ${VAR} or ${VAR:-default} with environment values."""

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        env_val = os.environ.get(var_name)
        if env_val is not None:
            return env_val
        if default is not None:
            return default
        return match.group(0)  # leave unresolved

    return _ENV_PATTERN.sub(_replace, value)


def _interpolate_recursive(data: Any) -> Any:
    """Recursively interpolate env vars in a data structure."""
    if isinstance(data, str):
        return _interpolate_env(data)
    if isinstance(data, dict):
        return {k: _interpolate_recursive(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_interpolate_recursive(item) for item in data]
    return data


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file with env var interpolation."""
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    if raw is None:
        return {}
    return _interpolate_recursive(raw)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when configuration is invalid."""

    def __init__(self, message: str, file: str = "", field: str = "") -> None:
        self.file = file
        self.field = field
        detail = message
        if file:
            detail = f"[{file}] {detail}"
        if field:
            detail = f"{detail} (field: {field})"
        super().__init__(detail)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RiskTier(str, Enum):
    """Risk tiers for tool calls."""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class RestartPolicy(str, Enum):
    """Server restart policy."""

    NEVER = "never"
    ON_FAILURE = "on_failure"
    ALWAYS = "always"


class TransportType(str, Enum):
    """MCP server transport type."""

    STDIO = "stdio"
    HTTP = "http"


class LogFormat(str, Enum):
    """Log output format."""

    JSON = "json"
    CONSOLE = "console"


class VoiceMode(str, Enum):
    """Voice activation mode."""

    PUSH_TO_TALK = "push_to_talk"
    WAKE_WORD = "wake_word"


class PolicyAction(str, Enum):
    """Policy decision action."""

    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


# ---------------------------------------------------------------------------
# Config Models — jarvis.yaml
# ---------------------------------------------------------------------------


class ModelConfig(BaseModel):
    """Configuration for a single LLM model role."""

    provider: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.3
    max_context: int | None = None  # provider-specific context window override

    model_config = ConfigDict(extra="forbid")


class ProviderConfig(BaseModel):
    """Configuration for an LLM provider."""

    api_key: str = ""
    base_url: str | None = None
    max_retries: int = 3
    timeout_seconds: int = 120

    model_config = ConfigDict(extra="forbid")


class ModelsConfig(BaseModel):
    """All model role assignments."""

    router_model: ModelConfig
    reasoning_model: ModelConfig
    summarizer_model: ModelConfig

    model_config = ConfigDict(extra="forbid")


class AgentConfig(BaseModel):
    """Agent loop configuration."""

    max_iterations: int = 12
    wall_clock_timeout_seconds: int = 300
    max_parallel_tool_calls: int = 5
    stream: bool = True

    model_config = ConfigDict(extra="forbid")


class SessionConfig(BaseModel):
    """Session and context assembly configuration."""

    token_budget: int = 100000
    conversation_window: int = 20
    summarize_after: int = 15

    model_config = ConfigDict(extra="forbid")


class MemoryConfig(BaseModel):
    """Memory system configuration."""

    enabled: bool = True
    store: str = "sqlite"
    embedding_model: str = "all-MiniLM-L6-v2"
    max_recall_items: int = 10
    auto_extract: bool = True

    model_config = ConfigDict(extra="forbid")


class ApiConfig(BaseModel):
    """API layer configuration."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8741
    token: str = ""

    model_config = ConfigDict(extra="forbid")

    @field_validator("host")
    @classmethod
    def _validate_host(cls, v: str) -> str:
        if v == "0.0.0.0":  # noqa: S104
            raise ValueError(
                "Binding to 0.0.0.0 is not allowed by default for security. "
                "Use 127.0.0.1 for local-only access."
            )
        return v


class VoiceConfig(BaseModel):
    """Voice pipeline configuration."""

    enabled: bool = False
    mode: VoiceMode = VoiceMode.PUSH_TO_TALK
    wake_word_model: str = "hey_jarvis"
    stt_model: str = "small"
    tts_voice: str = "en_US-lessac-medium"

    model_config = ConfigDict(extra="forbid")


class TriggerConfig(BaseModel):
    """Scheduled trigger configuration."""

    name: str
    schedule: str  # cron expression
    prompt: str
    tier_limit: RiskTier = RiskTier.L1
    enabled: bool = True

    model_config = ConfigDict(extra="forbid")


class GeneralConfig(BaseModel):
    """General application settings."""

    name: str = "JARVIS"
    data_dir: str = "~/.jarvis/data"
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.JSON

    model_config = ConfigDict(extra="forbid")

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid}")
        return v.upper()


class JarvisConfig(BaseModel):
    """Root configuration from jarvis.yaml."""

    general: GeneralConfig = GeneralConfig()
    models: ModelsConfig
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    agent: AgentConfig = AgentConfig()
    session: SessionConfig = SessionConfig()
    memory: MemoryConfig = MemoryConfig()
    api: ApiConfig = ApiConfig()
    voice: VoiceConfig = VoiceConfig()
    triggers: list[TriggerConfig] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Config Models — servers.yaml
# ---------------------------------------------------------------------------


class ToolOverride(BaseModel):
    """Per-tool risk tier and confirmation override."""

    risk: RiskTier
    confirm: bool | None = None

    model_config = ConfigDict(extra="forbid")


class ServerAuthConfig(BaseModel):
    """Authentication config for HTTP transport servers."""

    type: str = "bearer"
    token_env: str = ""

    model_config = ConfigDict(extra="forbid")


class ServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    name: str
    enabled: bool = True
    transport: TransportType = TransportType.STDIO
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    url: str | None = None
    auth: ServerAuthConfig | None = None
    env: dict[str, str] = Field(default_factory=dict)
    inherit_env: list[str] = Field(default_factory=list)
    timeout_seconds: int = 30
    startup_timeout_seconds: int = 15
    restart: RestartPolicy = RestartPolicy.ON_FAILURE
    toolsets: list[str] = Field(default_factory=list)
    risk_default: RiskTier = RiskTier.L3
    tool_overrides: dict[str, ToolOverride] = Field(default_factory=dict)
    expose: list[str] | None = None  # None = expose all

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_transport_fields(self) -> "ServerConfig":
        if self.transport == TransportType.STDIO:
            if not self.command:
                raise ValueError(
                    f"Server '{self.name}': stdio transport requires 'command'"
                )
        elif self.transport == TransportType.HTTP:
            if not self.url:
                raise ValueError(
                    f"Server '{self.name}': http transport requires 'url'"
                )
        return self


class ServersConfig(BaseModel):
    """Root configuration from servers.yaml."""

    servers: list[ServerConfig] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("servers")
    @classmethod
    def _unique_names(cls, v: list[ServerConfig]) -> list[ServerConfig]:
        names = [s.name for s in v]
        dupes = [n for n in names if names.count(n) > 1]
        if dupes:
            raise ValueError(f"Duplicate server names: {set(dupes)}")
        return v


# ---------------------------------------------------------------------------
# Config Models — policy.yaml
# ---------------------------------------------------------------------------


class RiskTierConfig(BaseModel):
    """Configuration for a single risk tier."""

    description: str = ""
    action: PolicyAction = PolicyAction.ALLOW
    audit: bool = True
    confirm: bool = False
    cooldown_seconds: int = 0

    model_config = ConfigDict(extra="forbid")


class PathSandboxConfig(BaseModel):
    """Path sandboxing rules."""

    enabled: bool = True
    allowed_roots: list[str] = Field(default_factory=list)
    resolve_symlinks: bool = True
    deny_patterns: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class UrlRulesConfig(BaseModel):
    """URL filtering rules."""

    enabled: bool = True
    block_private_ips: bool = True
    block_internal_ranges: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ShellRulesConfig(BaseModel):
    """Shell command rules."""

    enabled: bool = True
    allow_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ArgumentRulesConfig(BaseModel):
    """All argument-level safety rules."""

    path_sandboxing: PathSandboxConfig = PathSandboxConfig()
    url_rules: UrlRulesConfig = UrlRulesConfig()
    shell_rules: ShellRulesConfig = ShellRulesConfig()

    model_config = ConfigDict(extra="forbid")


class RateLimitConfig(BaseModel):
    """Rate limit configuration."""

    calls_per_minute: int = 60
    max_concurrent: int = 10

    model_config = ConfigDict(extra="forbid")


class RateLimitsConfig(BaseModel):
    """Global and per-server rate limits."""

    global_limits: RateLimitConfig = Field(
        default_factory=RateLimitConfig, alias="global"
    )
    per_server: dict[str, RateLimitConfig] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FinancialLimitsConfig(BaseModel):
    """Financial safety limits."""

    enabled: bool = False
    paper_trading_default: bool = True
    max_position_size: float = 0
    max_daily_loss: float = 0
    instrument_allow_list: list[str] = Field(default_factory=list)
    trading_hours_only: bool = True

    model_config = ConfigDict(extra="forbid")


class TaintConfig(BaseModel):
    """Taint tracking configuration."""

    enabled: bool = True
    untrusted_sources: list[str] = Field(default_factory=list)
    escalate_on_taint: bool = True

    model_config = ConfigDict(extra="forbid")


class QuietHoursConfig(BaseModel):
    """Quiet hours configuration."""

    enabled: bool = False
    start: str = "22:00"
    end: str = "07:00"
    timezone: str = "local"

    model_config = ConfigDict(extra="forbid")


class ProactiveConfig(BaseModel):
    """Proactive run restrictions."""

    max_tier: RiskTier = RiskTier.L1
    allow_notifications: bool = True
    quiet_hours: QuietHoursConfig = QuietHoursConfig()

    model_config = ConfigDict(extra="forbid")


class DefaultsConfig(BaseModel):
    """Default tier assignments."""

    unknown_tool_tier: RiskTier = RiskTier.L3
    unknown_server_tier: RiskTier = RiskTier.L3

    model_config = ConfigDict(extra="forbid")


class PolicyConfig(BaseModel):
    """Root configuration from policy.yaml."""

    risk_tiers: dict[str, RiskTierConfig] = Field(default_factory=dict)
    defaults: DefaultsConfig = DefaultsConfig()
    argument_rules: ArgumentRulesConfig = ArgumentRulesConfig()
    rate_limits: RateLimitsConfig = RateLimitsConfig()
    financial_limits: FinancialLimitsConfig = FinancialLimitsConfig()
    taint: TaintConfig = TaintConfig()
    proactive: ProactiveConfig = ProactiveConfig()

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Config Models — persona.yaml
# ---------------------------------------------------------------------------


class VoicePersonaConfig(BaseModel):
    """Voice-specific persona settings."""

    speed: float = 1.0
    voice_model: str = "en_US-lessac-medium"

    model_config = ConfigDict(extra="forbid")


class PersonaDefinition(BaseModel):
    """A single persona definition."""

    name: str
    description: str = ""
    formality: str = "balanced"
    humor: str = "minimal"
    brevity: str = "balanced"
    address_style: str = ""
    system_prompt_fragments: list[str] = Field(default_factory=list)
    voice: VoicePersonaConfig = VoicePersonaConfig()

    model_config = ConfigDict(extra="forbid")


class PersonaConfig(BaseModel):
    """Root configuration from persona.yaml."""

    active: str = "jarvis"
    personas: dict[str, PersonaDefinition] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_active_exists(self) -> "PersonaConfig":
        if self.active not in self.personas:
            available = list(self.personas.keys())
            raise ValueError(
                f"Active persona '{self.active}' not found. "
                f"Available: {available}"
            )
        return self

    def get_active(self) -> PersonaDefinition:
        """Get the active persona definition."""
        return self.personas[self.active]

    def get_system_prompt(self) -> str:
        """Build the complete system prompt from active persona fragments."""
        persona = self.get_active()
        return "\n\n".join(persona.system_prompt_fragments)


# ---------------------------------------------------------------------------
# Unified Config Loader
# ---------------------------------------------------------------------------


class AppConfig:
    """Unified application configuration loaded from all YAML files.

    Usage:
        config = AppConfig.load(config_dir=Path("config"))
    """

    def __init__(
        self,
        jarvis: JarvisConfig,
        servers: ServersConfig,
        policy: PolicyConfig,
        persona: PersonaConfig,
        config_dir: Path,
    ) -> None:
        self.jarvis = jarvis
        self.servers = servers
        self.policy = policy
        self.persona = persona
        self.config_dir = config_dir

    @classmethod
    def load(
        cls,
        config_dir: Path | None = None,
        env_file: Path | None = None,
    ) -> "AppConfig":
        """Load and validate all configuration files.

        Args:
            config_dir: Path to config directory. Defaults to ./config.
            env_file: Path to .env file. Defaults to .env in project root.

        Returns:
            Validated AppConfig instance.

        Raises:
            ConfigError: If any config file is invalid or missing.
        """
        if config_dir is None:
            config_dir = Path("config")

        # Load .env file
        if env_file is None:
            env_file = config_dir.parent / ".env"
        if env_file.exists():
            load_dotenv(env_file)

        errors: list[str] = []

        # Load each config file
        try:
            jarvis_data = _load_yaml(config_dir / "jarvis.yaml")
            jarvis = JarvisConfig(**jarvis_data)
        except Exception as e:
            errors.append(f"jarvis.yaml: {e}")
            jarvis = None  # type: ignore[assignment]

        try:
            servers_data = _load_yaml(config_dir / "servers.yaml")
            servers = ServersConfig(**servers_data)
        except Exception as e:
            errors.append(f"servers.yaml: {e}")
            servers = None  # type: ignore[assignment]

        try:
            policy_data = _load_yaml(config_dir / "policy.yaml")
            policy = PolicyConfig(**policy_data)
        except Exception as e:
            errors.append(f"policy.yaml: {e}")
            policy = None  # type: ignore[assignment]

        try:
            persona_data = _load_yaml(config_dir / "persona.yaml")
            persona = PersonaConfig(**persona_data)
        except Exception as e:
            errors.append(f"persona.yaml: {e}")
            persona = None  # type: ignore[assignment]

        if errors:
            detail = "\n  ".join(errors)
            raise ConfigError(
                f"Configuration validation failed:\n  {detail}"
            )

        return cls(
            jarvis=jarvis,
            servers=servers,
            policy=policy,
            persona=persona,
            config_dir=config_dir,
        )

    def get_data_dir(self) -> Path:
        """Get the resolved data directory, creating it if needed."""
        data_dir = Path(self.jarvis.general.data_dir).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir

    def get_enabled_servers(self) -> list[ServerConfig]:
        """Get list of enabled server configs."""
        return [s for s in self.servers.servers if s.enabled]
