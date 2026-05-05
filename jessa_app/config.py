from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
import os
import platform
import shutil
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

ClientIP = IPv4Address | IPv6Address
ClientNetwork = IPv4Network | IPv6Network


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def normalize_model(model: str | None) -> str:
    if not model:
        return "gpt-5.4-mini"
    aliases = {
        "chatgpt-5-4-mini": "gpt-5.4-mini",
        "gpt-5-4-mini": "gpt-5.4-mini",
        "chatgpt-5.4-mini": "gpt-5.4-mini",
    }
    return aliases.get(model.strip(), model.strip())


def _env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _parse_client_networks(value: str) -> tuple[ClientNetwork, ...]:
    networks: list[ClientNetwork] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ip_network(item, strict=False))
        except ValueError:
            continue
    if networks:
        return tuple(networks)
    return (ip_network("127.0.0.0/8"), ip_network("::1/128"))


def _normalize_client_ip(host: str | None) -> ClientIP | None:
    if not host:
        return None
    try:
        client_ip = ip_address(host.split("%", 1)[0])
    except ValueError:
        return None
    mapped = getattr(client_ip, "ipv4_mapped", None)
    return mapped or client_ip


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = normalize_model(os.getenv("OPENAI_MODEL"))
GEMINI_API_KEY = _env_first("GEMINI_API_KEY", "GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"
GEMINI_BASE_URL = (
    os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").strip().rstrip("/")
    or "https://generativelanguage.googleapis.com/v1beta"
)
GROK_API_KEY = _env_first("GROK_API_KEY", "XAI_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4.3").strip() or "grok-4.3"
GROK_BASE_URL = os.getenv("GROK_BASE_URL", "https://api.x.ai/v1").strip().rstrip("/") or "https://api.x.ai/v1"
CLAUDE_API_KEY = _env_first("CLAUDE_API_KEY", "ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5"
CLAUDE_BASE_URL = (
    os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com").strip().rstrip("/") or "https://api.anthropic.com"
)
CLAUDE_VERSION = os.getenv("CLAUDE_VERSION", "2023-06-01").strip() or "2023-06-01"
LLM_PROVIDER_PRIORITY = tuple(
    item.strip().lower()
    for item in os.getenv("JESSA_LLM_PROVIDER_PRIORITY", "openai,claude,gemini,grok").split(",")
    if item.strip()
)
if not LLM_PROVIDER_PRIORITY:
    LLM_PROVIDER_PRIORITY = ("openai", "claude", "gemini", "grok")

EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "").strip()
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", EMAIL_APP_PASSWORD).strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", EMAIL_USER).strip()
EMAIL_IMAP_HOST = os.getenv("EMAIL_IMAP_HOST", "imap.gmail.com").strip()
EMAIL_IMAP_PORT = _int("EMAIL_IMAP_PORT", 993)
EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com").strip()
EMAIL_SMTP_PORT = _int("EMAIL_SMTP_PORT", 587)
EMAIL_LOOKBACK_DAYS = _int("EMAIL_LOOKBACK_DAYS", 30)
EMAIL_MAX_FETCH = _int("EMAIL_MAX_FETCH", 50)
EMAIL_SMTP_TLS = _bool(os.getenv("EMAIL_SMTP_TLS"), True)

DEFAULT_ALLOWED_CLIENT_NETWORKS = "127.0.0.0/8,::1/128,10.0.0.0/8"
ALLOWED_CLIENT_NETWORKS = _parse_client_networks(
    os.getenv("JESSA_ALLOWED_CLIENT_NETWORKS", DEFAULT_ALLOWED_CLIENT_NETWORKS)
)

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "").strip()
POSTGRES_PORT = _int("POSTGRES_PORT", 5432)
POSTGRES_USER = os.getenv("POSTGRES_USER", "").strip()
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", os.getenv("POSTGRES_PASS", "")).strip()
POSTGRES_DB_NAME = os.getenv("POSTGRES_DB_NAME", os.getenv("POSTGRES_DB", "")).strip()
POSTGRES_SSLMODE = os.getenv("POSTGRES_SSLMODE", "prefer").strip() or "prefer"
POSTGRES_CONNECT_TIMEOUT = _int("POSTGRES_CONNECT_TIMEOUT", 10)
POSTGRES_MAINTENANCE_DB = os.getenv("POSTGRES_MAINTENANCE_DB", "postgres").strip() or "postgres"

DB_BACKEND = "postgresql"
SQLITE_IMPORT_PATH = (
    BASE_DIR / os.getenv("JESSA_SQLITE_IMPORT_PATH", os.getenv("JESSA_DB_PATH", "data/jessa.sqlite3"))
).resolve()
_PROFILE_SOURCE_VALUE = os.getenv("JESSA_PROFILE_SOURCE", "").strip()
PROFILE_SOURCE = (BASE_DIR / _PROFILE_SOURCE_VALUE).resolve() if _PROFILE_SOURCE_VALUE else None
LINKEDIN_PROFILE_URL = os.getenv("JESSA_LINKEDIN_PROFILE_URL", "").strip()
LINKEDIN_BROWSER_PROFILE_DIR = (
    BASE_DIR / os.getenv("JESSA_LINKEDIN_BROWSER_PROFILE_DIR", "data/linkedin-browser")
).expanduser().resolve()
LINKEDIN_PAGE_SETTLE_MS = _int("JESSA_LINKEDIN_PAGE_SETTLE_MS", 7000)
LINKEDIN_LOGIN_WAIT_MS = _int("JESSA_LINKEDIN_LOGIN_WAIT_MS", 180000)
LINKEDIN_PROFILE_CAPTURE_WAIT_MS = _int("JESSA_LINKEDIN_PROFILE_CAPTURE_WAIT_MS", 180000)
LINKEDIN_PROFILE_SCROLL_PASSES = _int("JESSA_LINKEDIN_PROFILE_SCROLL_PASSES", 8)
LINKEDIN_MIN_PROFILE_CONTENT_CHARS = _int("JESSA_LINKEDIN_MIN_PROFILE_CONTENT_CHARS", 400)


def _default_resume_dir() -> Path:
    home = Path.home()
    if platform.system() == "Darwin":
        return home / "Documents" / "job_hunting"
    return Path("/home/ironman/Documents/job_hunting")


RESUME_DIR = Path(os.getenv("JESSA_RESUME_DIR", str(_default_resume_dir()))).expanduser()


def _macos_chrome_path() -> str:
    path = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(path) if path.exists() else ""

PLAYWRIGHT_BROWSER_PATH = os.getenv("PLAYWRIGHT_BROWSER_PATH", "").strip()
if not PLAYWRIGHT_BROWSER_PATH:
    PLAYWRIGHT_BROWSER_PATH = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
        or _macos_chrome_path()
        or ""
    )


def allowed_client_networks() -> list[str]:
    return [str(network) for network in ALLOWED_CLIENT_NETWORKS]


def client_host_allowed(host: str | None) -> bool:
    client_ip = _normalize_client_ip(host)
    if client_ip is None:
        return False
    return any(client_ip in network for network in ALLOWED_CLIENT_NETWORKS)


def postgres_configured() -> bool:
    return bool(POSTGRES_HOST and POSTGRES_USER and POSTGRES_DB_NAME)


def llm_provider_configs() -> dict[str, dict[str, str]]:
    return {
        "openai": {"api_key": OPENAI_API_KEY, "model": OPENAI_MODEL, "base_url": ""},
        "gemini": {"api_key": GEMINI_API_KEY, "model": GEMINI_MODEL, "base_url": GEMINI_BASE_URL},
        "grok": {"api_key": GROK_API_KEY, "model": GROK_MODEL, "base_url": GROK_BASE_URL},
        "claude": {"api_key": CLAUDE_API_KEY, "model": CLAUDE_MODEL, "base_url": CLAUDE_BASE_URL},
    }


def configured_llm_providers() -> list[dict[str, str]]:
    providers = llm_provider_configs()
    ordered: list[dict[str, str]] = []
    seen: set[str] = set()
    for provider in LLM_PROVIDER_PRIORITY:
        if provider in seen:
            continue
        seen.add(provider)
        data = providers.get(provider)
        if data and data["api_key"]:
            ordered.append({"name": provider, **data})
    return ordered


def active_llm_provider() -> dict[str, str] | None:
    providers = configured_llm_providers()
    return providers[0] if providers else None


def llm_provider_status() -> dict[str, dict[str, str | bool]]:
    providers = llm_provider_configs()
    active = active_llm_provider()
    active_name = active["name"] if active else ""
    return {
        name: {
            "configured": bool(data["api_key"]),
            "active": name == active_name,
            "model": data["model"],
        }
        for name, data in providers.items()
    }
