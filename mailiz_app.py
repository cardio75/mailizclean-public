from __future__ import annotations

import socket
import time
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

from config.settings import DATA_DIR, FOLDERS, ROOT_DIR, USER_ENV_PATH
from mailiz_dashboard import DEFAULT_RULES_PATH, DashboardHandler


def find_available_port(host: str, preferred_port: int) -> int:
    if preferred_port > 0 and port_is_available(host, preferred_port):
        return preferred_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def ensure_user_files() -> None:
    for folder in FOLDERS.values():
        folder.mkdir(parents=True, exist_ok=True)

    if USER_ENV_PATH.exists():
        return

    template = ROOT_DIR / ".env.example"
    if template.exists():
        USER_ENV_PATH.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            USER_ENV_PATH.chmod(0o600)
        except OSError:
            pass
        return

    USER_ENV_PATH.write_text(
        "\n".join(
            [
                "MAILIZ_USER=",
                "MAILIZ_PASSWORD=",
                "OTP_EMAIL=",
                "OTP_PASSWORD=",
                "OTP_IMAP_SERVER=",
                "",
            ]
        ),
        encoding="utf-8",
    )
    try:
        USER_ENV_PATH.chmod(0o600)
    except OSError:
        pass


def run_app(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    ensure_user_files()
    selected_port = find_available_port(host, port)
    handler = type(
        "ConfiguredDashboardHandler",
        (DashboardHandler,),
        {"report_dir": FOLDERS["REPORTS"], "rules_file": DEFAULT_RULES_PATH},
    )
    server = ThreadingHTTPServer((host, selected_port), handler)
    url = f"http://{host}:{selected_port}/"

    print("MailizClean")
    print(f"Adresse du dashboard: {url}")
    print("Si le navigateur ne s'ouvre pas automatiquement, copiez cette adresse dans Chrome, Safari, Edge ou Firefox.")
    print(f"Dossier utilisateur: {DATA_DIR}")
    print(f"Configuration: {USER_ENV_PATH}")
    print("Fermez cette fenetre pour arreter MailizClean.")

    if open_browser:
        time.sleep(0.4)
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_app())
