from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config


@dataclass(frozen=True)
class EnvField:
    name: str
    label: str
    group: str
    kind: str = "text"
    secret: bool = False
    help: str = ""
    aliases: tuple[str, ...] = ()


ENV_FIELDS = (
    EnvField("JESSA_LLM_PROVIDER_PRIORITY", "Provider Priority", "LLM Providers", help="Comma-separated order, for example openai,claude,gemini,grok."),
    EnvField("OPENAI_API_KEY", "OpenAI API Key", "LLM Providers", secret=True),
    EnvField("OPENAI_MODEL", "OpenAI Model", "LLM Providers"),
    EnvField("CLAUDE_API_KEY", "Claude API Key", "LLM Providers", secret=True, help="ANTHROPIC_API_KEY is also accepted in .env.", aliases=("ANTHROPIC_API_KEY",)),
    EnvField("CLAUDE_MODEL", "Claude Model", "LLM Providers"),
    EnvField("GEMINI_API_KEY", "Gemini API Key", "LLM Providers", secret=True, help="GOOGLE_API_KEY is also accepted in .env.", aliases=("GOOGLE_API_KEY",)),
    EnvField("GEMINI_MODEL", "Gemini Model", "LLM Providers"),
    EnvField("GROK_API_KEY", "Grok API Key", "LLM Providers", secret=True, help="XAI_API_KEY is also accepted in .env.", aliases=("XAI_API_KEY",)),
    EnvField("GROK_MODEL", "Grok Model", "LLM Providers"),
    EnvField("POSTGRES_HOST", "Host", "PostgreSQL"),
    EnvField("POSTGRES_PORT", "Port", "PostgreSQL", kind="number"),
    EnvField("POSTGRES_USER", "User", "PostgreSQL"),
    EnvField("POSTGRES_PASS", "Password", "PostgreSQL", secret=True, aliases=("POSTGRES_PASSWORD",)),
    EnvField("POSTGRES_DB_NAME", "Database", "PostgreSQL", aliases=("POSTGRES_DB",)),
    EnvField("POSTGRES_SSLMODE", "SSL Mode", "PostgreSQL"),
    EnvField("EMAIL_USER", "Email User", "Email"),
    EnvField("EMAIL_APP_PASSWORD", "App Password", "Email", secret=True),
    EnvField("EMAIL_FROM", "From Address", "Email"),
    EnvField("EMAIL_IMAP_HOST", "IMAP Host", "Email"),
    EnvField("EMAIL_IMAP_PORT", "IMAP Port", "Email", kind="number"),
    EnvField("EMAIL_SMTP_HOST", "SMTP Host", "Email"),
    EnvField("EMAIL_SMTP_PORT", "SMTP Port", "Email", kind="number"),
    EnvField("EMAIL_LOOKBACK_DAYS", "Lookback Days", "Email", kind="number"),
    EnvField("EMAIL_MAX_FETCH", "Max Fetch", "Email", kind="number"),
    EnvField("EMAIL_SMTP_TLS", "SMTP TLS", "Email", kind="boolean"),
    EnvField("JESSA_HOST", "Listener Host", "Access", help="Used by start_jessa.sh after restart."),
    EnvField("JESSA_PORT", "Listener Port", "Access", kind="number", help="Used by start_jessa.sh and stop_jessa.sh after restart."),
    EnvField("JESSA_ALLOWED_CLIENT_NETWORKS", "Allowed Client Networks", "Access"),
    EnvField("PLAYWRIGHT_BROWSER_PATH", "Browser Path", "Paths and LinkedIn"),
    EnvField(
        "JESSA_PLAYWRIGHT_RENDERED_HEADLESS",
        "Rendered Headless",
        "Paths and LinkedIn",
        help="Use auto, true, or false. Auto is headless on Linux servers without DISPLAY.",
    ),
    EnvField("JESSA_RESUME_DIR", "Resume Directory", "Paths and LinkedIn"),
    EnvField("JESSA_SQLITE_IMPORT_PATH", "Legacy SQLite Import Path", "Paths and LinkedIn"),
    EnvField("JESSA_PROFILE_SOURCE", "Profile Seed File", "Paths and LinkedIn"),
    EnvField("JESSA_LINKEDIN_PROFILE_URL", "LinkedIn Profile URL", "Paths and LinkedIn"),
    EnvField("JESSA_LINKEDIN_BROWSER_PROFILE_DIR", "LinkedIn Browser Profile Dir", "Paths and LinkedIn"),
    EnvField("JESSA_LINKEDIN_PAGE_SETTLE_MS", "LinkedIn Page Settle ms", "Paths and LinkedIn", kind="number"),
    EnvField("JESSA_LINKEDIN_LOGIN_WAIT_MS", "LinkedIn Login Wait ms", "Paths and LinkedIn", kind="number"),
    EnvField("JESSA_LINKEDIN_PROFILE_CAPTURE_WAIT_MS", "LinkedIn Capture Wait ms", "Paths and LinkedIn", kind="number"),
    EnvField("JESSA_LINKEDIN_PROFILE_SCROLL_PASSES", "LinkedIn Scroll Passes", "Paths and LinkedIn", kind="number"),
    EnvField("JESSA_LINKEDIN_MIN_PROFILE_CONTENT_CHARS", "LinkedIn Min Profile Chars", "Paths and LinkedIn", kind="number"),
)

FIELD_BY_NAME = {field.name: field for field in ENV_FIELDS}
FIELD_BY_KEY = {
    key: field
    for field in ENV_FIELDS
    for key in (field.name, *field.aliases)
}


def env_path() -> Path:
    return config.BASE_DIR / ".env"


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, value.rstrip("\n")


def read_env_values(path: Path | None = None) -> dict[str, str]:
    target = path or env_path()
    values: dict[str, str] = {}
    if not target.exists():
        return values
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = _parse_env_line(line)
        if parsed:
            values[parsed[0]] = parsed[1]
    return values


def settings_payload() -> dict[str, Any]:
    values = read_env_values()
    groups: dict[str, list[dict[str, Any]]] = {}
    for field in ENV_FIELDS:
        value = values.get(field.name, "")
        if value == "":
            for alias in field.aliases:
                value = values.get(alias, "")
                if value:
                    break
        display_value = "" if field.secret else value
        groups.setdefault(field.group, []).append(
            {
                "name": field.name,
                "label": field.label,
                "kind": field.kind,
                "secret": field.secret,
                "value": display_value,
                "has_value": bool(value),
                "help": field.help,
            }
        )
    return {
        "path": str(env_path()),
        "groups": [{"name": name, "fields": fields} for name, fields in groups.items()],
        "restart_required": True,
    }


def _quote_env_value(value: str) -> str:
    if value == "":
        return ""
    if any(char.isspace() for char in value) or "#" in value or '"' in value or "'" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def update_env_values(updates: dict[str, str]) -> dict[str, Any]:
    allowed = {name: str(value) for name, value in updates.items() if name in FIELD_BY_NAME}
    if not allowed:
        return settings_payload()

    target = env_path()
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines() if target.exists() else []
    seen: set[str] = set()
    updated_lines: list[str] = []

    for line in lines:
        parsed = _parse_env_line(line)
        if not parsed:
            updated_lines.append(line)
            continue
        key, current_value = parsed
        field = FIELD_BY_KEY.get(key)
        if not field or field.name not in allowed:
            updated_lines.append(line)
            continue
        new_value = allowed[field.name]
        if field.secret and new_value == "":
            updated_lines.append(f"{key}={current_value}")
        else:
            updated_lines.append(f"{key}={_quote_env_value(new_value)}")
        seen.add(field.name)

    for field in ENV_FIELDS:
        if field.name not in allowed or field.name in seen:
            continue
        new_value = allowed[field.name]
        if field.secret and new_value == "":
            continue
        updated_lines.append(f"{field.name}={_quote_env_value(new_value)}")

    target.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")
    return settings_payload()
