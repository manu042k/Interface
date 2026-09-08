"""ST-001: environment-based configuration.

One rule: a missing *secret* is a hard, named failure at startup — never a silent
default. Non-secret knobs (timeouts, budgets) may default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise ConfigError(
            f"Missing required configuration: {key!r}. "
            f"Set it in your environment or .env (see backend/.env.example)."
        )
    return val


def _optional(key: str, default: str) -> str:
    return os.environ.get(key, default).strip() or default


def _int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Configuration {key!r} must be an integer, got {raw!r}") from exc


def _bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class Config:
    # LLM provider router (discovery only)
    llm_providers: list[str]
    providers: dict[str, ProviderConfig]

    # Target application
    target_base_url: str
    allowlist_path: Path

    # Storage
    db_path: Path
    evidence_root: Path

    # Budgets
    max_steps: int
    run_timeout_seconds: int
    action_timeout_seconds: int

    # Browser
    headed: bool

    extra: dict[str, str] = field(default_factory=dict)

    @property
    def offline(self) -> bool:
        """True when the only configured provider is the deterministic scripted one."""
        return self.llm_providers == ["scripted"]


_PROVIDER_ENV = {
    "openrouter": ("OPENROUTER_BASE_URL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL"),
    "nvidia_nim": ("NVIDIA_NIM_BASE_URL", "NVIDIA_NIM_API_KEY", "NVIDIA_NIM_MODEL"),
}


def load_config(dotenv_path: str | os.PathLike[str] | None = None, *, strict: bool = True) -> Config:
    """Load configuration from environment (+ optional .env file).

    strict=False skips provider-key validation — used by unit tests that never
    call a live model.
    """
    if dotenv_path is not None:
        load_dotenv(dotenv_path, override=False)
    else:
        # Load backend/.env if present, quietly.
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / ".env"
            if candidate.exists():
                load_dotenv(candidate, override=False)
                break

    providers_raw = _optional("CUA_LLM_PROVIDERS", "scripted")
    llm_providers = [p.strip() for p in providers_raw.split(",") if p.strip()]
    if not llm_providers:
        raise ConfigError("CUA_LLM_PROVIDERS must name at least one provider")

    providers: dict[str, ProviderConfig] = {}
    for name in llm_providers:
        if name == "scripted":
            providers[name] = ProviderConfig("scripted", "local://scripted", "n/a", "scripted")
            continue
        if name not in _PROVIDER_ENV:
            raise ConfigError(
                f"Unknown LLM provider {name!r}. Known: {sorted(_PROVIDER_ENV) + ['scripted']}"
            )
        base_env, key_env, model_env = _PROVIDER_ENV[name]
        if strict:
            api_key = _require(key_env)
        else:
            api_key = os.environ.get(key_env, "").strip() or "test-key"
        providers[name] = ProviderConfig(
            name=name,
            base_url=_optional(base_env, _default_base_url(name)),
            api_key=api_key,
            model=_optional(model_env, _default_model(name)),
        )

    return Config(
        llm_providers=llm_providers,
        providers=providers,
        target_base_url=_optional("CUA_TARGET_BASE_URL", "http://127.0.0.1:8799").rstrip("/"),
        allowlist_path=Path(_optional("CUA_ALLOWLIST_PATH", "config/allowlist.example.json")),
        db_path=Path(_optional("CUA_DB_PATH", ".data/cua.db")),
        evidence_root=Path(_optional("CUA_EVIDENCE_ROOT", ".data/evidence")),
        max_steps=_int("CUA_MAX_STEPS", 40),
        run_timeout_seconds=_int("CUA_RUN_TIMEOUT_SECONDS", 300),
        action_timeout_seconds=_int("CUA_ACTION_TIMEOUT_SECONDS", 15),
        headed=_bool("CUA_HEADED", False),
    )


def _default_base_url(name: str) -> str:
    return {
        "openrouter": "https://openrouter.ai/api/v1",
        "nvidia_nim": "https://integrate.api.nvidia.com/v1",
    }[name]


def _default_model(name: str) -> str:
    return {
        "openrouter": "anthropic/claude-sonnet-4",
        "nvidia_nim": "meta/llama-3.3-70b-instruct",
    }[name]
