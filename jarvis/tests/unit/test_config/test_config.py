"""Tests for configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.config import (
    AppConfig,
    ConfigError,
    JarvisConfig,
    PolicyConfig,
    PersonaConfig,
    RiskTier,
    ServersConfig,
    ServerConfig,
    TransportType,
    _interpolate_env,
)


class TestEnvInterpolation:
    """Test ${VAR} and ${VAR:-default} interpolation."""

    def test_simple_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_VAR", "hello")
        assert _interpolate_env("${TEST_VAR}") == "hello"

    def test_var_with_default(self) -> None:
        # Unset var with default
        result = _interpolate_env("${NONEXISTENT_VAR:-fallback}")
        assert result == "fallback"

    def test_var_set_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR", "real_value")
        assert _interpolate_env("${MY_VAR:-default}") == "real_value"

    def test_unresolved_var_stays(self) -> None:
        # Var not set, no default → left as-is
        result = _interpolate_env("${TOTALLY_MISSING_VAR}")
        assert result == "${TOTALLY_MISSING_VAR}"

    def test_multiple_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        result = _interpolate_env("${A}-${B}")
        assert result == "1-2"


class TestServerConfig:
    """Test server configuration validation."""

    def test_stdio_requires_command(self) -> None:
        with pytest.raises(ValueError, match="requires 'command'"):
            ServerConfig(name="test", transport=TransportType.STDIO, command=None)

    def test_http_requires_url(self) -> None:
        with pytest.raises(ValueError, match="requires 'url'"):
            ServerConfig(name="test", transport=TransportType.HTTP, url=None)

    def test_valid_stdio_server(self) -> None:
        server = ServerConfig(
            name="test",
            transport=TransportType.STDIO,
            command="python",
            args=["-m", "test_server"],
        )
        assert server.name == "test"
        assert server.risk_default == RiskTier.L3  # safe default

    def test_valid_http_server(self) -> None:
        server = ServerConfig(
            name="test",
            transport=TransportType.HTTP,
            url="http://127.0.0.1:8080/mcp",
        )
        assert server.transport == TransportType.HTTP

    def test_duplicate_names_rejected(self) -> None:
        with pytest.raises(ValueError, match="Duplicate server names"):
            ServersConfig(
                servers=[
                    ServerConfig(name="dupe", command="python"),
                    ServerConfig(name="dupe", command="python"),
                ]
            )

    def test_unknown_fields_rejected(self) -> None:
        with pytest.raises(Exception):
            ServerConfig(
                name="test",
                command="python",
                unknown_field="value",  # type: ignore
            )


class TestPolicyConfig:
    """Test policy configuration defaults."""

    def test_defaults_are_safe(self) -> None:
        from jarvis.config import DefaultsConfig

        defaults = DefaultsConfig()
        assert defaults.unknown_tool_tier == RiskTier.L3
        assert defaults.unknown_server_tier == RiskTier.L3


class TestPersonaConfig:
    """Test persona configuration."""

    def test_active_must_exist(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            PersonaConfig(
                active="nonexistent",
                personas={},
            )

    def test_system_prompt_assembly(self) -> None:
        from jarvis.config import PersonaDefinition

        persona = PersonaConfig(
            active="test",
            personas={
                "test": PersonaDefinition(
                    name="Test",
                    system_prompt_fragments=["Fragment 1.", "Fragment 2."],
                ),
            },
        )
        prompt = persona.get_system_prompt()
        assert "Fragment 1." in prompt
        assert "Fragment 2." in prompt


class TestApiConfig:
    """Test API configuration safety."""

    def test_rejects_0000(self) -> None:
        from jarvis.config import ApiConfig

        with pytest.raises(ValueError, match="0.0.0.0"):
            ApiConfig(host="0.0.0.0")

    def test_allows_localhost(self) -> None:
        from jarvis.config import ApiConfig

        config = ApiConfig(host="127.0.0.1")
        assert config.host == "127.0.0.1"


class TestAppConfig:
    """Test full config loading."""

    def test_load_real_config(self, config_dir: Path) -> None:
        """Load the actual project config files and validate them."""
        config = AppConfig.load(config_dir=config_dir)
        assert config.jarvis.general.name == "JARVIS"
        assert len(config.servers.servers) > 0
        assert config.policy.defaults.unknown_tool_tier == RiskTier.L3
        assert config.persona.active in config.persona.personas

    def test_missing_config_dir(self, tmp_path: Path) -> None:
        """Error when config files don't exist."""
        with pytest.raises(ConfigError, match="validation failed"):
            AppConfig.load(config_dir=tmp_path / "nonexistent")

    def test_enabled_servers(self, config_dir: Path) -> None:
        """Get only enabled servers."""
        config = AppConfig.load(config_dir=config_dir)
        enabled = config.get_enabled_servers()
        for s in enabled:
            assert s.enabled is True
