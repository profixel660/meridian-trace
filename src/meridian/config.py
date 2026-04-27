"""Process-level configuration."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_app_version() -> str:
    """Resolve the running app version from package metadata.

    Single source of truth is pyproject.toml's [project].version field;
    this avoids the per-release hand-edit of two places that drifted in
    alpha-1 (the wheel said 0.2.0a1 but the running app logged 0.1.0).
    """
    try:
        return _pkg_version("meridian")
    except PackageNotFoundError:
        return "0.0.0+source"


def _is_unsafe_cwd(p: Path) -> bool:
    """True if ``p`` looks like a Windows system directory we should never
    treat as the project root.

    Elevated-Admin shells default cwd to ``C:\\Windows\\System32``; an
    installed-wheel run from there used to silently route project state to
    ``C:\\Windows\\System32\\data\\projects\\...`` and PermissionError on
    first write (alpha-1 SME bug + alpha-2 installer bug — see
    ``project_v013_deferred.md``). Conservative match list — only the
    canonical Windows infrastructure paths.
    """
    parts = [s.lower() for s in p.parts]
    if "windows" in parts and (
        "system32" in parts or "syswow64" in parts or "system" in parts
    ):
        return True
    posix = p.as_posix().lower()
    if "/program files" in posix or "/programdata" in posix:
        return True
    return False


def _meridian_home() -> Path:
    """Per-machine Meridian data root — projects, .env, runtime/, install.log.

    Resolution order:
        1. ``MERIDIAN_HOME`` env var (operator override).
        2. ``C:\\Meridian`` on Windows when it exists (installer's canonical
           layout — every PowerShell-installed box has this).
        3. ``~/Meridian`` cross-platform fallback for dev / non-installer runs.
    """
    if env := os.environ.get("MERIDIAN_HOME"):
        return Path(env)
    if os.name == "nt":
        canonical = Path("C:/Meridian")
        if canonical.exists():
            return canonical
    return Path.home() / "Meridian"


def _project_root() -> Path:
    """Repo root for dev trees, Meridian home for installed wheels.

    Walks up from this file until ``pyproject.toml`` is found (dev-tree
    answer). Falls back to ``cwd()`` only when cwd is a sane working
    directory; if cwd is a Windows system path (System32 / Program Files /
    ProgramData) we substitute :func:`_meridian_home` instead. This is the
    fix for the alpha-1/alpha-2 elevated-admin install bug where the backend
    inherited ``C:\\Windows\\System32`` as cwd and tried to write logs and
    project DBs there.
    """
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    cwd = Path.cwd()
    if _is_unsafe_cwd(cwd):
        return _meridian_home()
    return cwd


def _bootstrap_env_from_dotenv() -> None:
    """Load ``.env`` (KEY=value lines) into os.environ for SDKs to pick up.

    ``.env`` WINS over any pre-existing os.environ value. This is the
    dev-friendly convention (matches python-dotenv default) and avoids
    confusion when stale Windows-level env vars mask a freshly-edited
    project ``.env``.

    Search order (first hit wins):
        1. ``<project_root>/.env`` (dev-tree convention).
        2. ``<MERIDIAN_HOME>/.env`` (installed-wheel convention — installer
           writes the API key here).

    Runs at module import so every code path that imports config sees the
    keys.
    """
    candidates: list[Path] = [_project_root() / ".env"]
    home_env = _meridian_home() / ".env"
    if home_env not in candidates:
        candidates.append(home_env)
    for env_path in candidates:
        if not env_path.exists():
            continue
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # Skip placeholder values so they never override real env-supplied keys.
                if not key or not value or value.startswith("PASTE-"):
                    continue
                os.environ[key] = value
        except OSError:
            pass
        return  # first found wins


_bootstrap_env_from_dotenv()


# --------------------------------------------------------------------------
# Per-purpose provider routing (design/PROVIDER_ROUTING_V1.md)
# --------------------------------------------------------------------------

PurposeRoute = tuple[str, str]  # (provider, model)

# The seven routable LLM purposes per llm_call.purpose enum.
DEFAULT_PURPOSE_ROUTING: dict[str, PurposeRoute] = {
    "quality_scan":        ("anthropic", "claude-sonnet-4-6"),
    "triage":              ("anthropic", "claude-haiku-4-5-20251001"),
    "extract_text_spec":   ("anthropic", "claude-sonnet-4-6"),
    "extract_demarcation": ("anthropic", "claude-sonnet-4-6"),
    "extract_bod":         ("anthropic", "claude-sonnet-4-6"),
    "conflict_pass":       ("anthropic", "claude-sonnet-4-6"),
    "error_explain":       ("anthropic", "claude-sonnet-4-6"),
}


# Local-route presets (named recipes shipped for onboarding / settings UI).
LOCAL_PRESETS: dict[str, dict[str, PurposeRoute]] = {
    # All-cloud: every purpose on Anthropic Sonnet 4.6 (Haiku 4.5 for triage).
    # Mirrors DEFAULT_PURPOSE_ROUTING; defined explicitly so it is selectable
    # via `routing apply` like any other preset (and resolvable from the
    # `cloud-default` operator-facing alias). CONTEXT.md §12.
    "cloud-sonnet-default": {
        "quality_scan":        ("anthropic", "claude-sonnet-4-6"),
        "triage":              ("anthropic", "claude-haiku-4-5-20251001"),
        "extract_text_spec":   ("anthropic", "claude-sonnet-4-6"),
        "extract_demarcation": ("anthropic", "claude-sonnet-4-6"),
        "extract_bod":         ("anthropic", "claude-sonnet-4-6"),
        "conflict_pass":       ("anthropic", "claude-sonnet-4-6"),
        "error_explain":       ("anthropic", "claude-sonnet-4-6"),
    },
    # 5090-class single-GPU workstation, Ollama running locally.
    "ollama-5090-balanced": {
        "triage":            ("ollama", "qwen2.5:14b-instruct"),
        "quality_scan":      ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "extract_text_spec": ("anthropic", "claude-sonnet-4-6"),  # keep cloud
        "extract_bod":       ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "conflict_pass":     ("anthropic", "claude-sonnet-4-6"),  # keep cloud
        "error_explain":     ("ollama", "qwen2.5:14b-instruct"),
    },
    # Air-gapped: NO cloud calls at all. Quality risks the user accepts.
    "ollama-air-gapped": {
        "triage":            ("ollama", "qwen2.5:14b-instruct"),
        "quality_scan":      ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "extract_text_spec": ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "extract_bod":       ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "conflict_pass":     ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "error_explain":     ("ollama", "qwen2.5:14b-instruct"),
    },
    # Cost-only: triage local, everything else cloud.
    "triage-local-only": {
        "triage": ("ollama", "qwen2.5:14b-instruct"),
        # other purposes inherit defaults (cloud)
    },
}


# Operator-facing preset names (CONTEXT.md §12 vocabulary) → technical preset
# name in LOCAL_PRESETS. The technical names describe WHAT the preset does
# (specific provider + model). The operator-facing names describe DEPLOYMENT
# INTENT and remain stable even if the underlying recipe is retuned (e.g. a
# new local model replaces qwen2.5 in `hybrid` without renaming the alias).
#
# `routing apply` accepts EITHER form. Lookup is alias-first, then technical.
# Existing project DBs that persist a technical name are unaffected — the
# stored value is used as-is and never rewritten by the alias layer.
PRESET_ALIASES: dict[str, str] = {
    "cloud-default": "cloud-sonnet-default",   # CONTEXT §12: every purpose on Sonnet (Haiku for triage)
    "hybrid":        "ollama-5090-balanced",   # CONTEXT §12: triage + lower-stakes local; load-bearing on cloud Sonnet
    "air-gapped":    "ollama-air-gapped",      # CONTEXT §12: every purpose on local; cloud blocked at preflight
}


# One-line human-readable description per technical preset, used by
# `routing list-presets` and any future settings UI. Keeping this beside
# LOCAL_PRESETS rather than inline in the CLI keeps the data layer in one
# place.
PRESET_DESCRIPTIONS: dict[str, str] = {
    "cloud-sonnet-default": "All purposes on Anthropic Sonnet 4.6 (Haiku 4.5 for triage). No local dependency.",
    "ollama-5090-balanced": "Triage + lower-stakes purposes on local Ollama (5090-class GPU); load-bearing extract_text_spec and conflict_pass stay on cloud Sonnet.",
    "ollama-air-gapped":    "Every purpose on local Ollama. No cloud calls; cloud routes blocked at preflight.",
    "triage-local-only":    "Only triage routed to local Ollama; everything else inherits the cloud defaults. Cost-control recipe.",
}


def resolve_preset_name(name: str) -> str | None:
    """Resolve `name` (operator alias OR technical name) to a LOCAL_PRESETS key.

    Returns the technical preset name if `name` is recognised; None otherwise.
    Three-outcome discipline: callers distinguish None (preset-not-found) from
    a returned name (preset-found; they then run their own validation).
    """
    if name in LOCAL_PRESETS:
        return name
    if name in PRESET_ALIASES:
        return PRESET_ALIASES[name]
    return None


# Providers considered "local" / on-prem for air-gap mode.
LOCAL_PROVIDERS: frozenset[str] = frozenset(
    {"ollama", "vllm", "openai-compatible", "localai", "local"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MERIDIAN_", env_file=".env", extra="ignore")

    # Paths
    project_root: Path = Field(default_factory=_project_root)
    data_dir: Path | None = None  # defaults to <project_root>/data/projects
    prompts_dir: Path | None = None  # defaults to <project_root>/prompts

    # LLM defaults (CONTEXT.md §12)
    fallback_max_tokens: int = 16000
    extraction_max_tokens: int = 64000  # Sonnet 4.x natively supports up to 64k output
    temperature: float = 0.0

    # Per-purpose routing. None = inherit from DEFAULT_PURPOSE_ROUTING.
    # Shape: {purpose_name: (provider, model)}
    purpose_routing: dict[str, tuple[str, str]] | None = None

    # Air-gap mode: when True, any non-local resolved route raises before
    # the network call (see meridian.llm.client.call_llm preflight).
    air_gapped: bool = False

    # Prompt versions (must match files in prompts/)
    prompt_text_spec_version: str = "v1.1"
    prompt_bod_version: str = "v1.1"
    prompt_quality_scan_version: str = "v1.0"

    # Tool versioning — sourced from package metadata (pyproject is canonical).
    # The default below is only used when imported from a non-installed source
    # tree where importlib.metadata cannot resolve the package.
    app_version: str = Field(default_factory=_resolve_app_version)
    tool_disclaimer_version: str = "v1-dev"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir or (self.project_root / "data" / "projects")

    @property
    def web_dir(self) -> Path | None:
        """Where to look for the bundled GUI wizard's static export.

        Resolution order mirrors :pyattr:`prompts_path`:
        1. ``MERIDIAN_WEB_DIR`` env var (operator override) — must exist on disk.
        2. ``<project_root>/apps/web/out`` if it exists — the dev-tree
           layout produced by ``npm run build``.
        3. ``<package>/_web`` — the in-wheel bundled copy (added by the
           hatch force-include rule in ``pyproject.toml``). This is what
           ``pip install``-ed users hit.
        4. None — caller logs a warning and serves API-only.
        """
        override_raw = os.environ.get("MERIDIAN_WEB_DIR")
        if override_raw:
            candidate = Path(override_raw).expanduser()
            return candidate if candidate.exists() else None
        repo_web = self.project_root / "apps" / "web" / "out"
        if repo_web.exists():
            return repo_web
        wheel_web = Path(__file__).resolve().parent / "_web"
        if wheel_web.exists():
            return wheel_web
        return None

    @property
    def prompts_path(self) -> Path:
        """Where to look for prompt files.

        Resolution order:
        1. Explicit ``MERIDIAN_PROMPTS_DIR`` (from settings).
        2. ``<project_root>/prompts`` if it exists — the dev-tree layout.
        3. ``<package>/_prompts`` — the in-wheel bundled copy (see
           ``[tool.hatch.build.targets.wheel.force-include]`` in
           ``pyproject.toml``). This is what ``pip install``-ed users hit.
        """
        if self.prompts_dir is not None:
            return self.prompts_dir
        repo_prompts = self.project_root / "prompts"
        if repo_prompts.exists():
            return repo_prompts
        return Path(__file__).resolve().parent / "_prompts"

    @property
    def anthropic_api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")

    @property
    def openai_api_key(self) -> str | None:
        return os.environ.get("OPENAI_API_KEY")

    def resolve_route(
        self,
        purpose: str,
        *,
        project_routing: dict[str, tuple[str, str]] | None = None,
    ) -> tuple[str, str]:
        """Resolve (provider, model) for a purpose using the override hierarchy.

        Order (highest wins):
          1. Env var MERIDIAN_ROUTE_<PURPOSE>=provider/model
          2. project_routing[purpose] (read at job/call time from app_setting)
          3. self.purpose_routing[purpose]
          4. DEFAULT_PURPOSE_ROUTING[purpose]

        Per-call provider/model arguments to call_llm sit ABOVE this entire
        chain — they are applied by call_llm before resolve_route runs.
        """
        # 1. Env var
        env_key = f"MERIDIAN_ROUTE_{purpose.upper()}"
        raw = os.environ.get(env_key)
        if raw and "/" in raw:
            provider, _, model = raw.partition("/")
            provider = provider.strip()
            model = model.strip()
            if provider and model:
                return provider, model

        # 2. Project-level routing
        if project_routing and purpose in project_routing:
            entry = project_routing[purpose]
            return entry[0], entry[1]

        # 3. Process-level Settings.purpose_routing
        if self.purpose_routing and purpose in self.purpose_routing:
            entry = self.purpose_routing[purpose]
            return entry[0], entry[1]

        # 4. Defaults
        if purpose not in DEFAULT_PURPOSE_ROUTING:
            raise KeyError(
                f"Unknown LLM purpose '{purpose}' — no default route. "
                f"Known purposes: {sorted(DEFAULT_PURPOSE_ROUTING)}"
            )
        return DEFAULT_PURPOSE_ROUTING[purpose]


settings = Settings()


__all__ = [
    "DEFAULT_PURPOSE_ROUTING",
    "LOCAL_PRESETS",
    "LOCAL_PROVIDERS",
    "PRESET_ALIASES",
    "PRESET_DESCRIPTIONS",
    "PurposeRoute",
    "Settings",
    "resolve_preset_name",
    "settings",
]
