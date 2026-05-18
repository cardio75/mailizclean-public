import os
import sys
from pathlib import Path

from dotenv import load_dotenv


APP_NAME = "MailizClean"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent.parent.absolute()


def user_data_dir() -> Path:
    if not is_frozen():
        return resource_root() / "data"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / APP_NAME
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME


ROOT_DIR = resource_root()
DATA_DIR = user_data_dir()
USER_ENV_PATH = DATA_DIR / ".env"
CONFIG_KEYS = (
    "MAILIZ_USER",
    "MAILIZ_PASSWORD",
    "OTP_EMAIL",
    "OTP_PASSWORD",
    "OTP_IMAP_SERVER",
)
PLACEHOLDER_VALUES = {
    "votre_adresse@medecin.mssante.fr",
    "votre_mot_de_passe",
    "votre_adresse_otp@example.com",
    "mot_de_passe_application",
    "imap.example.com",
}


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def is_placeholder_value(value: str) -> bool:
    return value.strip() in PLACEHOLDER_VALUES


def load_environment() -> None:
    # Local development uses the repo .env. Packaged builds prefer the user data dir.
    load_dotenv(ROOT_DIR / ".env")
    for key, value in read_env_file(USER_ENV_PATH).items():
        if key in CONFIG_KEYS and value and not is_placeholder_value(value):
            os.environ[key] = value


load_environment()

FOLDERS = {
    "LOGS": DATA_DIR / "logs",
    "TEMP": DATA_DIR / "temp",
    "REPORTS": DATA_DIR / "reports",
}

for folder in FOLDERS.values():
    folder.mkdir(parents=True, exist_ok=True)

SECRETS = {
    "mailiz": {
        "user": os.getenv("MAILIZ_USER"),
        "password": os.getenv("MAILIZ_PASSWORD"),
    },
    "otp_email": {
        "email": os.getenv("OTP_EMAIL"),
        "password": os.getenv("OTP_PASSWORD"),
        "imap_server": os.getenv("OTP_IMAP_SERVER"),
    },
}

TIMEOUTS = {
    "page_load": 60000,
    "element_wait": 20000,
    "global_operation": 300,
    "typing_delay": 0.2,
}


def current_config_status() -> dict:
    load_environment()
    values = {key: os.getenv(key) or "" for key in CONFIG_KEYS}
    missing = [key for key, value in values.items() if not value or is_placeholder_value(value)]
    return {
        "path": str(USER_ENV_PATH),
        "configured": not missing,
        "values": {
            "MAILIZ_USER": "" if is_placeholder_value(values["MAILIZ_USER"]) else values["MAILIZ_USER"],
            "MAILIZ_PASSWORD_SET": bool(values["MAILIZ_PASSWORD"]) and not is_placeholder_value(values["MAILIZ_PASSWORD"]),
            "OTP_EMAIL": "" if is_placeholder_value(values["OTP_EMAIL"]) else values["OTP_EMAIL"],
            "OTP_PASSWORD_SET": bool(values["OTP_PASSWORD"]) and not is_placeholder_value(values["OTP_PASSWORD"]),
            "OTP_IMAP_SERVER": "" if is_placeholder_value(values["OTP_IMAP_SERVER"]) else values["OTP_IMAP_SERVER"],
        },
        "missing": missing,
    }


def write_user_config(values: dict) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = read_env_file(USER_ENV_PATH)

    for key in CONFIG_KEYS:
        value = str(values.get(key, "")).strip()
        if value:
            existing[key] = value

    USER_ENV_PATH.write_text(render_env_file(existing), encoding="utf-8")
    try:
        USER_ENV_PATH.chmod(0o600)
    except OSError:
        pass
    load_environment()
    return current_config_status()


def render_env_file(values: dict[str, str]) -> str:
    lines = [
        "# Identifiants Mailiz",
        f"MAILIZ_USER={values.get('MAILIZ_USER', '')}",
        f"MAILIZ_PASSWORD={values.get('MAILIZ_PASSWORD', '')}",
        "",
        "# Boite email recevant les codes OTP Mailiz",
        f"OTP_EMAIL={values.get('OTP_EMAIL', '')}",
        f"OTP_PASSWORD={values.get('OTP_PASSWORD', '')}",
        f"OTP_IMAP_SERVER={values.get('OTP_IMAP_SERVER', '')}",
        "",
    ]
    return "\n".join(lines)


def validate_required_secrets() -> None:
    load_environment()
    SECRETS["mailiz"]["user"] = os.getenv("MAILIZ_USER")
    SECRETS["mailiz"]["password"] = os.getenv("MAILIZ_PASSWORD")
    SECRETS["otp_email"]["email"] = os.getenv("OTP_EMAIL")
    SECRETS["otp_email"]["password"] = os.getenv("OTP_PASSWORD")
    SECRETS["otp_email"]["imap_server"] = os.getenv("OTP_IMAP_SERVER")

    missing_vars = []

    if not SECRETS["mailiz"]["user"]:
        missing_vars.append("MAILIZ_USER")
    if is_placeholder_value(SECRETS["mailiz"]["user"] or ""):
        missing_vars.append("MAILIZ_USER")
    if not SECRETS["mailiz"]["password"]:
        missing_vars.append("MAILIZ_PASSWORD")
    if is_placeholder_value(SECRETS["mailiz"]["password"] or ""):
        missing_vars.append("MAILIZ_PASSWORD")
    if not SECRETS["otp_email"]["email"]:
        missing_vars.append("OTP_EMAIL")
    if is_placeholder_value(SECRETS["otp_email"]["email"] or ""):
        missing_vars.append("OTP_EMAIL")
    if not SECRETS["otp_email"]["password"]:
        missing_vars.append("OTP_PASSWORD")
    if is_placeholder_value(SECRETS["otp_email"]["password"] or ""):
        missing_vars.append("OTP_PASSWORD")
    if not SECRETS["otp_email"]["imap_server"]:
        missing_vars.append("OTP_IMAP_SERVER")
    if is_placeholder_value(SECRETS["otp_email"]["imap_server"] or ""):
        missing_vars.append("OTP_IMAP_SERVER")

    if missing_vars:
        raise ValueError(
            "Configuration incomplete. Variables manquantes : "
            + ", ".join(missing_vars)
            + f". Fichier a renseigner : {USER_ENV_PATH}"
        )
