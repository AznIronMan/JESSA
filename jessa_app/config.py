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

DB_PATH = (BASE_DIR / os.getenv("JESSA_DB_PATH", "data/jessa.sqlite3")).resolve()
PROFILE_SOURCE = (BASE_DIR / os.getenv("JESSA_PROFILE_SOURCE", "jessa_gpt_instructions.txt")).resolve()

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
