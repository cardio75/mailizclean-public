from __future__ import annotations

import json
import re
import datetime as dt
import asyncio
import contextlib
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from config.settings import TIMEOUTS, current_config_status, validate_required_secrets, write_user_config
from mailiz_cleaner import (
    DEFAULT_FOLDERS,
    DEFAULT_RULES_PATH,
    DEFAULT_TRASH_FOLDER,
    append_move_log,
    apply_local_filters,
    batched,
    collect_paginated_results,
    create_cleanup_plan,
    deduplicate_items,
    get_mailbox_quota,
    get_trash_status,
    login_mailiz,
    load_cleanup_profiles,
    MAILIZ_URL,
    move_uid_to_trash,
    move_uids_to_trash,
    MOVE_BATCH_SIZE,
    open_folder,
    OTHER_KEYWORD,
    purge_trash,
    show_unfiltered_folder,
    write_reports,
)


class MailizDashboardSession:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.lock = threading.Lock()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.auth_mode = None
        self.progress_lock = threading.Lock()
        self.progress = {
            "phase": "idle",
            "message": "Prêt.",
            "current": None,
            "total": None,
            "updated_at": "",
        }

    def set_progress(
        self,
        phase: str,
        message: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        with self.progress_lock:
            self.progress = {
                "phase": phase,
                "message": message,
                "current": current,
                "total": total,
                "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            }

    def progress_snapshot(self) -> dict:
        with self.progress_lock:
            return dict(self.progress)

    def run(self, coro, timeout: int = 1800):
        with self.lock:
            future = asyncio.run_coroutine_threadsafe(coro, self.loop)
            return future.result(timeout=timeout)

    async def ensure_page(self, auth_mode: str = "automatic"):
        auth_mode = validate_auth_mode(auth_mode)
        if self.page and not self.page.is_closed() and self.auth_mode == auth_mode:
            return self.page
        if self.page and not self.page.is_closed():
            await self.close_browser()

        return await self.open_page(auth_mode)

    async def open_page(self, auth_mode: str):
        if auth_mode == "automatic":
            validate_required_secrets()
        self.set_progress("login", "Ouverture de Chromium.")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=False)
        self.context = await self.browser.new_context(accept_downloads=False)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(TIMEOUTS["element_wait"])
        if auth_mode == "manual":
            await self.wait_for_manual_login(self.page)
        else:
            self.set_progress("login", "Connexion Mailiz en cours.")
            await login_mailiz(self.page)
            self.set_progress("ready", "Session Mailiz connectée.")
        self.auth_mode = auth_mode
        return self.page

    async def close_browser(self) -> None:
        for target in (self.context, self.browser):
            if target:
                with contextlib.suppress(Exception):
                    await target.close()
        if self.playwright:
            with contextlib.suppress(Exception):
                await self.playwright.stop()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.auth_mode = None

    async def wait_for_manual_login(self, page) -> None:
        self.set_progress(
            "login",
            "Une fenêtre Mailiz est ouverte. Connectez-vous, puis revenez ici quand vous voyez votre boîte de réception. Dès qu'elle est détectée, MailizClean démarre automatiquement l'action demandée.",
        )
        await page.goto(MAILIZ_URL, wait_until="domcontentloaded")
        try:
            await page.locator("#layout-sidebar, #mailboxlist").first.wait_for(timeout=15 * 60 * 1000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                "Connexion manuelle non détectée. Relancez l'action puis revenez ici quand la boîte de réception Mailiz est visible."
            ) from exc
        self.set_progress("ready", "Connexion manuelle détectée. Démarrage automatique de l'action.")

    async def scan_mailbox(
        self,
        report_dir: Path,
        prefix: str = "mailiz-scan",
        folders: list[str] | None = None,
        auth_mode: str = "automatic",
    ) -> dict:
        page = await self.ensure_page(auth_mode)
        folders = folders or list(DEFAULT_FOLDERS)
        all_items = []
        summaries = []
        self.set_progress("scan", "Lecture du quota Mailiz.")
        quota = await get_mailbox_quota(page)

        for folder in folders:
            self.set_progress("scan", f"Ouverture du dossier {dashboard_mailbox_label(folder)}.")
            await open_folder(page, folder)
            await show_unfiltered_folder(page)
            items, summary = await collect_paginated_results(
                page,
                folder,
                OTHER_KEYWORD,
                0,
                filter_keyword=False,
                progress_callback=self.scan_progress,
            )
            all_items.extend(items)
            summaries.append(summary)

        all_items = apply_local_filters(deduplicate_items(all_items), None)
        self.set_progress("scan", f"Écriture du rapport : {len(all_items)} message(s).")
        paths = write_reports(all_items, quota, report_dir, prefix, summaries)
        folder_label = ", ".join(dashboard_mailbox_label(folder) for folder in folders)
        self.set_progress("ready", f"Scan {folder_label} terminé : {len(all_items)} message(s).")
        return {
            "count": len(all_items),
            "json": paths["json"].name,
            "csv": paths["csv"].name,
            "quota": quota.raw or "",
            "folders": folders,
            "folder_label": folder_label,
        }

    def scan_progress(self, folder: str, scope: str, page_index: int, page_count) -> None:
        if getattr(page_count, "total", None):
            message = f"Scan {folder} : {page_count.raw}"
            current = getattr(page_count, "end", None)
            total = getattr(page_count, "total", None)
        else:
            message = f"Scan {folder} : page {page_index}"
            current = page_index
            total = None
        self.set_progress("scan", message, current, total)

    async def move_to_trash(self, items: list[dict], source: str, auth_mode: str = "automatic") -> dict:
        page = await self.ensure_page(auth_mode)
        moved = []
        missing = []
        total = len(items)
        self.set_progress("move", f"Préparation de la mise en corbeille : {total} message(s).", 0, total)
        by_folder: dict[str, list[dict]] = {}
        for item in items:
            by_folder.setdefault(item.get("folder", ""), []).append(item)

        processed = 0
        for folder, folder_items in by_folder.items():
            self.set_progress("move", f"Ouverture du dossier {folder}.", processed, total)
            await open_folder(page, folder)
            await show_unfiltered_folder(page)
            for batch in batched(folder_items, MOVE_BATCH_SIZE):
                batch_uids = [str(item.get("uid", "")).strip() for item in batch]
                batch_uids = [uid for uid in batch_uids if uid]
                self.set_progress(
                    "move",
                    f"Mise en corbeille par lot {processed + 1}-{processed + len(batch)}/{total}.",
                    processed + len(batch),
                    total,
                )
                if len(batch_uids) == len(batch) and await move_uids_to_trash(page, folder, batch_uids):
                    moved.extend(batch)
                    processed += len(batch)
                    continue

                self.set_progress(
                    "move",
                    f"Lot refusé, reprise message par message {processed + 1}/{total}.",
                    processed,
                    total,
                )
                for item in batch:
                    uid = str(item.get("uid", "")).strip()
                    self.set_progress("move", f"Mise en corbeille {processed + 1}/{total}.", processed + 1, total)
                    if uid and await move_uid_to_trash(page, folder, uid):
                        moved.append(item)
                    else:
                        missing.append(item)
                    processed += 1

        self.set_progress("move", "Vérification de la corbeille.", processed, total)
        trash_status = await get_trash_status(page, DEFAULT_TRASH_FOLDER, 1, debug=False)
        append_move_log(
            {
                "at": dt.datetime.now().isoformat(timespec="seconds"),
                "action": "move_to_trash_dashboard_session",
                "selection": source,
                "requested_count": len(items),
                "moved_count": len(moved),
                "missing_count": len(missing),
                "final_trash_status": trash_status,
                "moved": [{"folder": item.get("folder"), "uid": item.get("uid"), "subject": item.get("subject", "")} for item in moved],
                "missing": [{"folder": item.get("folder"), "uid": item.get("uid"), "subject": item.get("subject", "")} for item in missing],
            }
        )
        self.set_progress("ready", f"Mise en corbeille terminée : {len(moved)}/{total}.")
        return {"moved": moved, "missing": missing, "trash_status": trash_status}

    async def empty_trash(
        self,
        report_dir: Path | None = None,
        folders: list[str] | None = None,
        auth_mode: str = "automatic",
    ) -> dict:
        page = await self.ensure_page(auth_mode)
        self.set_progress("empty", "Lecture du quota avant vidage.")
        quota_before = await get_mailbox_quota(page)
        self.set_progress("empty", "Lecture de la corbeille.")
        before = await get_trash_status(page, DEFAULT_TRASH_FOLDER, 1, debug=False)
        if not before.get("empty"):
            count = before.get("reported_count") if before.get("reported_count") is not None else before.get("scanned_count", 0)
            self.set_progress("empty", f"Vidage de la corbeille : {count} message(s).")
            await purge_trash(page, DEFAULT_TRASH_FOLDER)
        self.set_progress("empty", "Vérification de la corbeille après vidage.")
        after = await get_trash_status(page, DEFAULT_TRASH_FOLDER, 1, debug=False)
        self.set_progress("empty", "Lecture du quota après vidage.")
        quota_after = await get_mailbox_quota(page)
        if report_dir:
            self.set_progress("scan", "Scan de contrôle après vidage.")
        post_scan = await self.scan_mailbox(
            report_dir,
            "mailiz-scan-apres-nettoyage",
            folders=folders,
            auth_mode=auth_mode,
        ) if report_dir else None
        return {
            "before": before,
            "after": after,
            "quota_before": quota_before.raw or "",
            "quota_after": quota_after.raw or "",
            "post_scan": post_scan,
        }


MAILIZ_SESSION = MailizDashboardSession()

DASHBOARD_MAILBOXES = {
    "INBOX": "Messages reçus",
    "Sent": "Messages envoyés",
}

DASHBOARD_AUTH_MODES = {
    "automatic": "Connexion automatique",
    "manual": "Connexion manuelle",
}


def dashboard_mailbox_label(folder: str) -> str:
    return DASHBOARD_MAILBOXES.get(folder, folder)


def validate_dashboard_mailbox(folder: str | None) -> str:
    selected = folder or DEFAULT_FOLDERS[0]
    if selected not in DASHBOARD_MAILBOXES:
        raise ValueError(f"dossier non autorise depuis le dashboard: {selected}")
    return selected


def validate_auth_mode(auth_mode: str | None) -> str:
    selected = auth_mode or "automatic"
    if selected not in DASHBOARD_AUTH_MODES:
        raise ValueError(f"mode de connexion invalide: {selected}")
    return selected


HTML_PAGE = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MailizClean</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f7;
      --panel: #ffffff;
      --line: #d8dddd;
      --text: #172123;
      --muted: #5f6b70;
      --accent: #176b74;
      --accent-soft: #d9eef0;
      --warn: #9b5d00;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
    }
    main { padding: 16px 18px 24px; }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(260px, 2fr) minmax(150px, 1fr) minmax(150px, 1fr);
      gap: 10px;
      align-items: end;
      margin-bottom: 14px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }
    .primary-actions {
      display: grid;
      grid-template-columns: minmax(180px, 220px) minmax(220px, 1fr) auto minmax(260px, 1fr);
      gap: 10px;
      align-items: end;
      margin-bottom: 14px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }
    label, .field-label {
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    select, input {
      width: 100%;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      padding: 8px 9px;
      border-radius: 6px;
      font: inherit;
    }
    input[type="checkbox"] {
      width: auto;
      padding: 0;
    }
    .checkbox-field {
      min-height: 38px;
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--text);
      font-size: 13px;
      font-weight: 650;
    }
    .cards {
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .plan-actions {
      display: none;
      grid-template-columns: minmax(360px, 2fr) minmax(160px, 0.7fr) minmax(150px, 0.7fr) auto minmax(260px, 1fr);
      gap: 10px;
      align-items: end;
      margin-bottom: 14px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }
    .advanced-filters, .optional-settings {
      margin: -4px 0 14px;
      color: var(--muted);
    }
    .advanced-filters summary, .optional-settings summary {
      cursor: pointer;
      font-weight: 700;
      color: var(--accent);
    }
    .advanced-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(160px, 1fr));
      gap: 10px;
      margin-top: 10px;
      padding: 10px 12px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .scenario-field {
      display: grid;
      gap: 8px;
    }
    .profile-cards {
      display: grid;
      grid-template-columns: repeat(3, minmax(160px, 1fr));
      gap: 8px;
    }
    .profile-card {
      min-height: 88px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfc;
      color: var(--text);
      text-align: left;
      font-weight: 600;
      cursor: pointer;
    }
    .profile-card strong {
      display: block;
      margin-bottom: 5px;
      font-size: 14px;
    }
    .profile-card span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }
    .profile-card.active {
      border-color: var(--accent);
      background: var(--accent-soft);
    }
    .auth-mode-cards {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .auth-mode-card {
      min-height: 78px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfc;
      color: var(--text);
      text-align: left;
      font-weight: 600;
      cursor: pointer;
    }
    .auth-mode-card strong {
      display: block;
      margin-bottom: 5px;
      font-size: 14px;
    }
    .auth-mode-card span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
    }
    .auth-mode-card.active {
      border-color: var(--accent);
      background: var(--accent-soft);
    }
    .browser-notice {
      grid-column: 1 / -1;
      margin: 0;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfc;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }
    .plan-edit-actions {
      display: none;
      grid-template-columns: auto auto auto auto minmax(260px, 1fr);
      gap: 10px;
      align-items: end;
      margin: 14px 0;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }
    .step-heading {
      grid-column: 1 / -1;
      display: grid;
      gap: 2px;
      margin-bottom: 2px;
    }
    .step-kicker {
      color: var(--accent);
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
    }
    .step-heading h2 {
      margin: 0;
      color: var(--text);
      font-size: 16px;
    }
    .step-heading p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
    }
    .history-panel {
      margin: -4px 0 14px;
      color: var(--muted);
    }
    .history-panel summary {
      cursor: pointer;
      font-weight: 700;
      color: var(--accent);
    }
    .history-grid {
      display: grid;
      grid-template-columns: minmax(160px, 0.8fr) minmax(280px, 2fr);
      gap: 10px;
      margin-top: 10px;
      padding: 10px 12px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .review-heading {
      margin: 2px 0 10px;
    }
    button {
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      border-radius: 6px;
      padding: 9px 12px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button:disabled {
      opacity: 0.55;
      cursor: default;
    }
    button.secondary {
      background: var(--panel);
      color: var(--accent);
    }
    button.danger {
      border-color: #9f2f2f;
      background: #9f2f2f;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      min-height: 62px;
    }
    .card .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    .card .value {
      margin-top: 4px;
      font-size: 20px;
      font-weight: 700;
    }
    .table-wrap {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: auto;
      max-height: calc(100vh - 260px);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 1220px;
    }
    th, td {
      padding: 8px 9px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    th {
      position: sticky;
      top: 0;
      z-index: 2;
      background: #eef2f2;
      font-size: 12px;
      color: #334044;
    }
    tbody tr:hover { background: #f2f8f8; }
    .pill {
      display: inline-block;
      padding: 2px 7px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    .muted { color: var(--muted); }
    .subject {
      max-width: 360px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .notice {
      color: var(--warn);
      font-weight: 600;
    }
    .run-output {
      display: none;
      white-space: pre-wrap;
      overflow: auto;
      max-height: 180px;
      margin: -4px 0 14px;
      padding: 10px 12px;
      background: #102024;
      color: #e8f2f2;
      border-radius: 8px;
      font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .rules {
      display: none;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 14px;
      color: var(--muted);
    }
    .rules-main {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto minmax(220px, 1fr);
      gap: 10px;
      align-items: end;
      margin-top: 10px;
      padding: 10px;
      border: 1px solid var(--accent);
      border-radius: 8px;
      background: var(--accent-soft);
    }
    .rules-main label {
      margin: 0;
    }
    .rules-main-note {
      color: var(--text);
      font-size: 13px;
      font-weight: 650;
    }
    .rules-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(160px, 1fr));
      gap: 10px;
      margin-top: 8px;
    }
    .rule-item {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 9px;
      background: #fbfcfc;
    }
    .rule-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .rule-value {
      margin-top: 2px;
      color: var(--text);
      font-weight: 650;
    }
    .rules h2 {
      margin: 0;
      font-size: 15px;
      color: var(--text);
    }
    .rules p {
      margin: 6px 0 0;
    }
    .config-panel {
      margin-bottom: 14px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
    }
    .config-panel summary {
      cursor: pointer;
      font-weight: 800;
      color: var(--accent);
    }
    .config-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(180px, 1fr));
      gap: 10px;
      margin-top: 10px;
    }
    .config-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 10px;
      flex-wrap: wrap;
    }
    @media (max-width: 980px) {
      .toolbar { grid-template-columns: 1fr; }
      .primary-actions { grid-template-columns: 1fr; }
      .plan-actions { grid-template-columns: 1fr; }
      .advanced-grid, .profile-cards, .auth-mode-cards, .config-grid, .history-grid, .rules-grid, .rules-main { grid-template-columns: 1fr; }
      .cards { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>MailizClean</h1>
    <div class="muted">Parcours guidé de nettoyage Mailiz</div>
  </header>
  <main>
    <details class="config-panel" id="configPanel">
      <summary id="configSummary">Configuration Mailiz</summary>
      <div class="config-grid">
        <label>Adresse MSSanté
          <input id="mailizUserInput" type="email" autocomplete="username" placeholder="prenom.nom@medecin.mssante.fr">
        </label>
        <label>Mot de passe Mailiz
          <input id="mailizPasswordInput" type="password" autocomplete="current-password" placeholder="Laisser vide pour conserver">
        </label>
        <label>Email ordinaire qui reçoit le code OTP
          <input id="otpEmailInput" type="email" autocomplete="email" placeholder="adresse mail ordinaire renseignee dans Mailiz">
        </label>
        <label>Mot de passe email OTP
          <input id="otpPasswordInput" type="password" autocomplete="current-password" placeholder="Laisser vide pour conserver">
        </label>
        <label>Serveur IMAP OTP
          <input id="otpImapInput" type="text" placeholder="imap.example.com">
        </label>
      </div>
      <div class="config-actions">
        <button id="saveConfigButton" type="button">Enregistrer la configuration</button>
        <span class="muted" id="configStatus">Configuration non vérifiée.</span>
      </div>
    </details>

    <section class="primary-actions">
      <div class="step-heading">
        <div class="step-kicker">Étape 1</div>
        <h2>Choisir la boîte à analyser</h2>
        <p>Le scan lit les messages et prépare une analyse locale, sans modifier Mailiz.</p>
      </div>
      <label>Boîte à scanner
        <select id="scanMailboxSelect">
          <option value="INBOX">Messages reçus</option>
          <option value="Sent">Messages envoyés</option>
        </select>
      </label>
      <div class="scenario-field">
        <div class="field-label">Connexion à Mailiz</div>
        <select id="authModeSelect" hidden>
          <option value="automatic">Automatique avec identifiants enregistrés</option>
          <option value="manual">Manuelle dans une fenêtre Mailiz</option>
        </select>
        <div class="auth-mode-cards" id="authModeCards"></div>
      </div>
      <p class="browser-notice">Une fenêtre Chromium va s’ouvrir pour accéder à Mailiz. En connexion manuelle, utilisez-la pour vous connecter jusqu’à voir votre boîte de réception. Ensuite, vous pouvez la minimiser, mais laissez MailizClean travailler dedans sans cliquer dans cette fenêtre.</p>
      <button id="scanMailboxButton" type="button">Scanner la boîte</button>
      <div class="muted" id="scanStatus">Utiliser le dernier scan ou lancer une nouvelle analyse.</div>
    </section>

    <section class="plan-actions" id="planActions">
      <div class="step-heading">
        <div class="step-kicker">Étape 2</div>
        <h2>Préparer une proposition</h2>
        <p>Après un scan, cliquez ici pour afficher ou mettre à jour la liste des messages proposés.</p>
      </div>
      <div class="scenario-field">
        <div class="field-label">Scénario</div>
        <select id="profileSelect" hidden></select>
        <div class="profile-cards" id="profileCards"></div>
      </div>
      <label>Date limite
        <input id="beforeOverride" type="date">
      </label>
      <label class="checkbox-field">
        <input id="includeUnreadOverride" type="checkbox">
        Inclure les non lus
      </label>
      <button id="createPlanButton" type="button">Voir / mettre à jour la proposition</button>
      <div class="muted" id="planActionStatus">Cette étape prépare seulement la liste. Aucun message Mailiz ne sera modifié.</div>
      <details class="optional-settings">
        <summary>Options locales</summary>
        <div class="advanced-grid">
          <label>Nom interne
            <input id="planName" type="text" placeholder="optionnel">
          </label>
        </div>
      </details>
    </section>

    <details class="history-panel">
      <summary>Historique et affichage avancé</summary>
      <div class="history-grid">
        <label>Afficher
          <select id="modeSelect">
            <option value="scan">Analyse scannée</option>
            <option value="plan">Proposition de nettoyage</option>
          </select>
        </label>
        <label>Historique
          <select id="reportSelect"></select>
        </label>
      </div>
    </details>

    <section class="review-heading">
      <div class="step-heading">
        <div class="step-kicker">Étape 3</div>
        <h2>Vérifier et cocher les messages</h2>
        <p>Cochez tout ou partie des lignes à nettoyer. Seuls les messages cochés pourront être envoyés dans la corbeille.</p>
      </div>
    </section>

    <div class="toolbar">
      <label>Recherche
        <input id="textFilter" type="search" placeholder="patient, sujet, expediteur">
      </label>
      <label>Type
        <select id="typeFilter"></select>
      </label>
      <label>Pieces jointes
        <select id="attachmentFilter">
          <option value="">Toutes</option>
          <option value="true">Avec</option>
          <option value="false">Sans</option>
        </select>
      </label>
    </div>

    <details class="advanced-filters">
      <summary>Filtres avancés</summary>
      <div class="advanced-grid">
        <label>Origine technique
          <select id="keywordFilter"></select>
        </label>
        <label>Dossier
          <select id="folderFilter"></select>
        </label>
      </div>
    </details>

    <section class="cards">
      <div class="card"><div class="label">Messages affichés</div><div class="value" id="visibleCount">0</div></div>
      <div class="card"><div class="label">Messages sélectionnés</div><div class="value" id="selectedCount">0</div></div>
      <div class="card"><div class="label" id="quotaOrCutoffLabel">Quota</div><div class="value" id="quota">-</div></div>
      <div class="card"><div class="label">Total analyse</div><div class="value" id="totalCount">0</div></div>
      <div class="card"><div class="label">Volume affiché</div><div class="value" id="visibleSize">-</div></div>
      <div class="card"><div class="label">Volume sélection</div><div class="value" id="selectedSize">-</div></div>
    </section>

    <section class="rules" id="rulesBox"></section>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th><input id="selectAll" type="checkbox" aria-label="Selectionner les lignes visibles"></th>
            <th>Date</th>
            <th>Type</th>
            <th>Patient</th>
            <th>Expediteur</th>
            <th>Sujet</th>
            <th>Taille</th>
            <th>Lu</th>
            <th>Piece jointe</th>
            <th>Dossier</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>

    <section class="plan-edit-actions" id="planEditActions">
      <div class="step-heading">
        <div class="step-kicker">Étape 4</div>
        <h2>Mettre en corbeille, puis finaliser</h2>
        <p>La mise en corbeille reste réversible tant que la corbeille n’est pas vidée.</p>
      </div>
      <input id="recreateBeforeOverride" type="hidden">
      <input id="recreatePlanName" type="hidden">
      <button id="recreatePlanButton" class="secondary" type="button">Mettre à jour la proposition</button>
      <button id="runCleanupButton" class="danger" type="button">Mettre en corbeille</button>
      <button id="emptyTrashButton" class="danger" type="button" disabled>Vider la corbeille</button>
      <button id="deletePlanButton" class="secondary" type="button">Retirer de l’historique</button>
      <div class="muted" id="planEditStatus">Cochez les lignes voulues dans la proposition avant de lancer la mise en corbeille.</div>
    </section>
    <pre class="run-output" id="cleanupOutput"></pre>
  </main>

<script>
let reports = [];
let profiles = [];
let mode = 'scan';
let report = null;
let rows = [];
let visibleRows = [];
let selected = new Set();
let lastTrashCount = null;

const el = (id) => document.getElementById(id);
const mailboxLabels = {
  INBOX: 'Messages reçus',
  Sent: 'Messages envoyés',
};
const authModes = [
  {
    value: 'automatic',
    title: 'Connexion automatique',
    summary: 'Identifiants enregistrés, OTP récupéré par email.',
  },
  {
    value: 'manual',
    title: 'Connexion manuelle',
    summary: 'Vous vous connectez dans Mailiz, puis l’action démarre automatiquement.',
  },
];

function mailboxLabel(value) {
  return mailboxLabels[value] || value || 'Boîte';
}

function authMode() {
  return el('authModeSelect')?.value || 'automatic';
}

function authModeText() {
  if (authMode() === 'manual') {
    return 'Une fenêtre Mailiz va s’ouvrir. Connectez-vous comme d’habitude, choisissez SMS ou email pour recevoir le code, puis revenez ici quand vous voyez votre boîte de réception. Dès que MailizClean détecte la boîte, l’action démarre automatiquement. Vous pouvez alors minimiser cette fenêtre : à partir de là, il ne faut plus y toucher pendant l’action.';
  }
  return 'Connexion automatique avec les identifiants enregistrés. Une fenêtre Mailiz va s’ouvrir : vous pouvez la minimiser, mais ne cliquez pas dedans pendant l’action.';
}

function renderAuthModeCards() {
  const container = el('authModeCards');
  if (!container) return;
  container.innerHTML = '';
  for (const modeItem of authModes) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `auth-mode-card ${authMode() === modeItem.value ? 'active' : ''}`;
    button.innerHTML = `
      <strong>${escapeHtml(modeItem.title)}</strong>
      <span>${escapeHtml(modeItem.summary)}</span>
    `;
    button.addEventListener('click', () => {
      el('authModeSelect').value = modeItem.value;
      renderAuthModeCards();
    });
    container.appendChild(button);
  }
}

async function loadConfig() {
  try {
    const res = await fetch('/api/config');
    const payload = await res.json();
    if (!res.ok || payload.error) throw new Error(payload.error || 'Erreur inconnue');
    applyConfigStatus(payload);
  } catch (error) {
    el('configStatus').textContent = `Configuration indisponible : ${error.message}`;
  }
}

function applyConfigStatus(payload) {
  const values = payload.values || {};
  el('mailizUserInput').value = values.MAILIZ_USER || '';
  el('otpEmailInput').value = values.OTP_EMAIL || '';
  el('otpImapInput').value = values.OTP_IMAP_SERVER || '';
  el('mailizPasswordInput').value = '';
  el('otpPasswordInput').value = '';
  el('mailizPasswordInput').placeholder = values.MAILIZ_PASSWORD_SET ? 'Déjà enregistré' : 'Mot de passe Mailiz';
  el('otpPasswordInput').placeholder = values.OTP_PASSWORD_SET ? 'Déjà enregistré' : 'Mot de passe email ou app password';

  if (payload.configured) {
    el('configSummary').textContent = 'Configuration Mailiz prête';
    el('configStatus').textContent = 'Configuration enregistrée localement.';
    el('configPanel').open = false;
  } else {
    el('configSummary').textContent = 'Configuration Mailiz à compléter';
    el('configStatus').textContent = `Champs manquants : ${(payload.missing || []).join(', ') || 'aucun'}.`;
    el('configPanel').open = true;
  }
}

async function saveConfig() {
  const button = el('saveConfigButton');
  const status = el('configStatus');
  button.disabled = true;
  status.textContent = 'Enregistrement...';
  try {
    const payload = {
      MAILIZ_USER: el('mailizUserInput').value,
      MAILIZ_PASSWORD: el('mailizPasswordInput').value,
      OTP_EMAIL: el('otpEmailInput').value,
      OTP_PASSWORD: el('otpPasswordInput').value,
      OTP_IMAP_SERVER: el('otpImapInput').value,
    };
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await res.json();
    if (!res.ok || result.error) throw new Error(result.error || 'Erreur inconnue');
    applyConfigStatus(result);
  } catch (error) {
    status.textContent = `Erreur : ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function loadReports() {
  const res = await fetch(`/api/reports?kind=${encodeURIComponent(mode)}`);
  reports = await res.json();
  const select = el('reportSelect');
  select.innerHTML = '';
  for (const item of reports) {
    const option = document.createElement('option');
    option.value = item.name;
    option.textContent = reportChoiceLabel(item, select.options.length);
    option.title = item.name;
    select.appendChild(option);
  }
  if (reports.length) {
    await loadReport(reports[0].name);
  } else {
    report = null;
    rows = [];
    selected = new Set();
    populateFilters();
    render();
  }
}

async function loadProfiles() {
  const res = await fetch('/api/profiles');
  profiles = await res.json();
  const select = el('profileSelect');
  select.innerHTML = '';
  if (!Array.isArray(profiles) || !profiles.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'Aucun profil disponible';
    select.appendChild(option);
    renderProfileCards();
    return;
  }
  for (const profile of profiles) {
    const option = document.createElement('option');
    option.value = profile.name;
    option.textContent = profile.label || profile.name;
    option.title = profile.description || '';
    select.appendChild(option);
  }
  renderProfileCards();
  updateProfileFields();
}

async function loadReport(name) {
  const res = await fetch(`/api/report?name=${encodeURIComponent(name)}`);
  report = await res.json();
  rows = report.items || [];
  const reportFolder = inferReportFolder(report, rows);
  if (reportFolder && mailboxLabels[reportFolder]) {
    el('scanMailboxSelect').value = reportFolder;
  }
  selected = new Set();
  populateFilters();
  render();
}

function inferReportFolder(payload, items) {
  const summaries = payload?.summaries || [];
  if (summaries.length && summaries[0].folder) return summaries[0].folder;
  const firstWithFolder = (items || []).find(row => row.folder);
  return firstWithFolder?.folder || '';
}

function populateFilters() {
  fillFilter('keywordFilter', rows.map(r => r.matched_keywords || r.keyword || ''));
  fillFilter('typeFilter', rows.map(r => r.document_type || ''), documentTypeLabel);
  fillFilter('folderFilter', rows.map(r => r.folder || ''));
}

function fillFilter(id, values, labelFn = (value) => value) {
  const select = el(id);
  const current = select.value;
  const unique = Array.from(new Set(values.filter(Boolean))).sort();
  select.innerHTML = '<option value="">Tous</option>';
  for (const value of unique) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = labelFn(value);
    select.appendChild(option);
  }
  if (unique.includes(current)) select.value = current;
}

function reportChoiceLabel(item, index) {
  const prefix = mode === 'scan'
    ? (index === 0 ? 'Dernier scan' : 'Ancienne analyse')
    : (index === 0 ? 'Dernière proposition' : 'Proposition précédente');
  const when = formatDateTime(item.generated_at || item.mtime);
  const countLabel = `${item.count || 0} message${Number(item.count || 0) > 1 ? 's' : ''}`;
  const folderLabel = item.folder_label ? `${item.folder_label} - ` : '';
  return `${prefix}${when ? ` du ${when}` : ''} - ${folderLabel}${countLabel}`;
}

function formatDateTime(value) {
  if (!value) return '';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('fr-FR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function rowKey(row) {
  return `${row.folder || ''}|${row.uid || row.message_id || row.subject}`;
}

function matches(row) {
  const text = el('textFilter').value.trim().toLowerCase();
  const keyword = el('keywordFilter').value;
  const type = el('typeFilter').value;
  const folder = el('folderFilter').value;
  const attachment = el('attachmentFilter').value;
  const haystack = [
    row.patient, row.subject, row.sender, row.sender_email, row.uid, row.date
  ].join(' ').toLowerCase();

  if (text && !haystack.includes(text)) return false;
  if (keyword && (row.matched_keywords || row.keyword) !== keyword) return false;
  if (type && row.document_type !== type) return false;
  if (folder && row.folder !== folder) return false;
  if (attachment && String(row.has_attachment) !== attachment) return false;
  return true;
}

function render() {
  el('planActions').style.display = mode === 'scan' ? 'grid' : 'none';
  el('planEditActions').style.display = mode === 'plan' ? 'grid' : 'none';
  updatePlanEditFields();
  visibleRows = rows.filter(matches);
  renderRules();
  const tbody = el('rows');
  tbody.innerHTML = '';
  const fragment = document.createDocumentFragment();

  for (const row of visibleRows) {
    const tr = document.createElement('tr');
    const key = rowKey(row);
    tr.innerHTML = `
      <td><input type="checkbox" data-key="${escapeAttr(key)}" ${selected.has(key) ? 'checked' : ''}></td>
      <td>${escapeHtml(row.date || '')}</td>
      <td>${escapeHtml(documentTypeLabel(row.document_type || ''))}</td>
      <td>${escapeHtml(row.patient || '')}</td>
      <td>${escapeHtml(row.sender || '')}<div class="muted">${escapeHtml(row.sender_email || '')}</div></td>
      <td class="subject" title="${escapeAttr(row.subject || '')}">${escapeHtml(row.subject || '')}</td>
      <td>${escapeHtml(row.size || '')}</td>
      <td>${row.unread ? '<span class="notice">Non lu</span>' : 'Lu'}</td>
      <td>${row.has_attachment ? 'Oui' : 'Non'}</td>
      <td>${escapeHtml(row.folder || '')}</td>
    `;
    fragment.appendChild(tr);
  }
  tbody.appendChild(fragment);
  tbody.querySelectorAll('input[type=checkbox]').forEach(input => {
    input.addEventListener('change', () => {
      if (input.checked) selected.add(input.dataset.key);
      else selected.delete(input.dataset.key);
      updateStats();
    });
  });
  updateStats();
}

function renderRules() {
  const box = el('rulesBox');
  if (!report || mode !== 'plan') {
    box.style.display = 'none';
    box.innerHTML = '';
    return;
  }
  const rules = report.rules || {};
  const skipped = report.skipped || {};
  const skippedTotal = Object.values(skipped).reduce((sum, value) => sum + Number(value || 0), 0);
  const quotaWarning = report.quota_condition_met === false && rules.quota_over != null
    ? `Quota actuel sous le seuil de déclenchement (${rules.quota_over}%). La proposition reste affichée pour vérification.`
    : '';
  const projection = report.quota_projection || {};
  const effectText = projection.projected_percent == null
    ? 'estimation indisponible'
    : `${formatPercent(projection.current_percent)} -> ${formatPercent(projection.projected_percent)}`;
  const targetStatus = projection.target_under == null
    ? ''
    : projection.target_reached
      ? 'Avec la proposition complète, objectif probablement atteint.'
      : `Avec la proposition complète, objectif probablement non atteint, manque environ ${formatBytes(projection.missing_bytes_to_target || 0)}.`;
  const cutoff = rules.before || '';
  const dateText = cutoff ? `Messages avant le ${cutoff}` : 'Aucune date limite';
  const typesText = (rules.types || []).length ? rules.types.map(documentTypeLabel).join(', ') : 'tous';
  const groupsText = (rules.groups || []).length ? rules.groups.join(', ') : 'tous';
  const sizeText = formatStoredSize(report.candidate_size);
  const ruleName = rules.profile_label || rules.profile || 'personnalisee';
  const hasFilters = hasActiveFilters();
  const visibleBytes = sumRowBytes(visibleRows);
  const visibleTargetStatus = quotaTargetStatusForBytes(visibleBytes);
  const filterText = activeFilterText();
  box.style.display = 'block';
  box.innerHTML = `
    <h2>Proposition à vérifier</h2>
    <p>${escapeHtml(report.candidate_count || 0)} candidats retenus sur ${escapeHtml(report.source_count || 0)} messages analysés, pour environ ${escapeHtml(sizeText)}.</p>
    <div class="rules-main">
      <label>Date limite de la proposition
        <input id="planCutoffInput" type="date" value="${escapeAttr(cutoff)}">
      </label>
      <button id="planCutoffUpdateButton" class="secondary" type="button">Mettre à jour</button>
      <div class="rules-main-note">Critère principal : ${escapeHtml(dateText)}.</div>
    </div>
    ${hasFilters ? `<p class="notice">Filtres actifs : ${escapeHtml(filterText)}. Les cartes du haut et le tableau décrivent la vue filtrée ; les règles ci-dessous décrivent la proposition complète.</p>` : ''}
    <div class="rules-grid">
      ${hasFilters ? `<div class="rule-item"><div class="rule-label">Vue filtrée</div><div class="rule-value">${escapeHtml(visibleRows.length)} messages · ${escapeHtml(formatBytes(visibleBytes))}</div></div>` : ''}
      <div class="rule-item"><div class="rule-label">Règle</div><div class="rule-value">${escapeHtml(ruleName)}</div></div>
      <div class="rule-item"><div class="rule-label">Types de messages</div><div class="rule-value">${escapeHtml(typesText)}</div></div>
      <div class="rule-item"><div class="rule-label">Messages non lus</div><div class="rule-value">${rules.include_unread ? 'inclus' : 'exclus'}</div></div>
      <div class="rule-item"><div class="rule-label">Messages écartés</div><div class="rule-value">${escapeHtml(skippedTotal)}</div></div>
    </div>
    ${rules.profile_description ? `<p>${escapeHtml(rules.profile_description)}</p>` : ''}
    <p class="muted">Quota : ${escapeHtml(effectText)}. ${escapeHtml(quotaWarning || '')} ${escapeHtml(targetStatus || '')}${hasFilters && visibleTargetStatus ? ` ${escapeHtml(visibleTargetStatus)}` : ''}</p>
    <p class="muted">Écartés : ${escapeHtml(skipped.date ?? 0)} trop récents, ${escapeHtml(skipped.type ?? 0)} type non autorisé, ${escapeHtml(skipped.group ?? 0)} origine non autorisée, ${escapeHtml(skipped.unread ?? 0)} non lus, ${escapeHtml(skipped.missing_date ?? 0)} date absente. Source : ${escapeHtml(fileName(report.source_report || ''))}. Origine technique : ${escapeHtml(groupsText)}.</p>
  `;
  const cutoffInput = el('planCutoffInput');
  const cutoffButton = el('planCutoffUpdateButton');
  if (cutoffInput) {
    cutoffInput.addEventListener('change', () => {
      el('recreateBeforeOverride').value = cutoffInput.value;
    });
  }
  if (cutoffButton) {
    cutoffButton.addEventListener('click', () => {
      el('recreateBeforeOverride').value = cutoffInput?.value || '';
      recreateCurrentPlan();
    });
  }
}

function updateStats() {
  for (const key of Array.from(selected)) {
    if (!rows.some(row => rowKey(row) === key)) selected.delete(key);
  }
  const selectedRows = rows.filter(row => selected.has(rowKey(row)));
  el('visibleCount').textContent = visibleRows.length;
  el('selectedCount').textContent = selectedRows.length;
  el('totalCount').textContent = report?.candidate_count ?? rows.length;
  const quota = report?.quota?.percent;
  if (mode === 'plan') {
    el('quotaOrCutoffLabel').textContent = 'Date limite';
    el('quota').textContent = report?.rules?.before || '-';
  } else {
    el('quotaOrCutoffLabel').textContent = 'Quota';
    el('quota').textContent = quota == null ? '-' : `${quota}%`;
  }
  el('visibleSize').textContent = formatBytes(sumRowBytes(visibleRows));
  el('selectedSize').textContent = selectedRows.length ? formatBytes(sumRowBytes(selectedRows)) : '-';
  el('selectAll').checked = visibleRows.length > 0 && visibleRows.every(r => selected.has(rowKey(r)));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll('`', '&#096;');
}

function fileName(value) {
  return String(value).split('/').pop() || value;
}

function selectedOptionText(id) {
  const select = el(id);
  return select.selectedOptions[0]?.textContent || select.value;
}

function progressText(progress) {
  if (!progress || !progress.message) return '';
  let text = progress.message;
  if (progress.total && progress.current != null) {
    text += ` (${progress.current}/${progress.total})`;
  }
  return text;
}

function startProgress(statusEl, outputEl) {
  async function poll() {
    try {
      const res = await fetch('/api/progress');
      const payload = await res.json();
      const text = progressText(payload);
      if (!text) return;
      statusEl.textContent = text;
      if (outputEl) outputEl.textContent = text;
    } catch (error) {
      // Le résultat final de l'action gardera l'erreur utile si le polling échoue.
    }
  }
  poll();
  return window.setInterval(poll, 1000);
}

function stopProgress(timer) {
  if (timer) window.clearInterval(timer);
}

function hasActiveFilters() {
  return Boolean(
    el('textFilter').value.trim()
    || el('typeFilter').value
    || el('keywordFilter').value
    || el('folderFilter').value
    || el('attachmentFilter').value
  );
}

function activeFilterText() {
  const parts = [];
  const text = el('textFilter').value.trim();
  if (text) parts.push(`recherche "${text}"`);
  if (el('typeFilter').value) parts.push(`type ${selectedOptionText('typeFilter')}`);
  if (el('keywordFilter').value) parts.push(`origine ${selectedOptionText('keywordFilter')}`);
  if (el('folderFilter').value) parts.push(`dossier ${selectedOptionText('folderFilter')}`);
  if (el('attachmentFilter').value) parts.push(`pièces jointes ${selectedOptionText('attachmentFilter').toLowerCase()}`);
  return parts.join(' · ');
}

function quotaEffectForBytes(bytes) {
  const projection = report?.quota_projection || {};
  if (!projection.used_bytes || !projection.total_bytes) return 'estimation indisponible';
  const current = projection.current_percent ?? ((projection.used_bytes / projection.total_bytes) * 100);
  const projected = Math.max(projection.used_bytes - bytes, 0) / projection.total_bytes * 100;
  return `${formatPercent(current)} -> ${formatPercent(projected)}`;
}

function quotaTargetStatusForBytes(bytes) {
  const projection = report?.quota_projection || {};
  if (projection.target_under == null || !projection.used_bytes || !projection.total_bytes) return '';
  const projected = Math.max(projection.used_bytes - bytes, 0) / projection.total_bytes * 100;
  if (projected < projection.target_under) {
    return 'Avec les lignes affichées, objectif probablement atteint.';
  }
  const targetUsed = projection.total_bytes * (projection.target_under / 100);
  const missing = Math.max((projection.used_bytes - targetUsed) - bytes, 0);
  return `Avec les lignes affichées, objectif probablement non atteint, manque environ ${formatBytes(missing)}.`;
}

function documentTypeLabel(value) {
  const labels = {
    'biologie': 'Biologie',
    'imagerie': 'Imagerie',
    'consultation': 'Consultation / compte rendu',
    'synthese-sejour': 'Synthèse de séjour',
    'lettre': 'Lettre',
    'compte-rendu': 'Compte rendu',
    'autre': 'Autre / message simple',
    'inconnu': 'Inconnu',
  };
  return labels[value] || value;
}

function renderProfileCards() {
  const container = el('profileCards');
  if (!container) return;
  container.innerHTML = '';
  if (!profiles.length) {
    container.innerHTML = '<div class="muted">Aucun scénario disponible.</div>';
    return;
  }
  for (const profile of profiles) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `profile-card ${profile.name === el('profileSelect').value ? 'active' : ''}`;
    button.innerHTML = `
      <strong>${escapeHtml(profile.label || profile.name)}</strong>
      <span>${escapeHtml(profileSummary(profile))}</span>
    `;
    button.addEventListener('click', () => {
      el('profileSelect').value = profile.name;
      updateProfileFields();
      renderProfileCards();
    });
    container.appendChild(button);
  }
}

function profileSummary(profile) {
  const types = (profile.types || []).map(documentTypeLabel).join(', ') || 'tous types';
  const date = profile.before ? `avant le ${profile.before}` : 'sans date limite';
  const unread = profile.include_unread ? 'non lus inclus' : 'non lus exclus';
  const quota = profile.quota_over == null ? 'sans seuil quota' : `si quota > ${profile.quota_over}%`;
  return `${types} - ${date} - ${unread} - ${quota}`;
}

el('modeSelect').addEventListener('change', async (event) => {
  mode = event.target.value;
  await loadReports();
});
el('reportSelect').addEventListener('change', (event) => loadReport(event.target.value));
el('profileSelect').addEventListener('change', () => {
  updateProfileFields();
  renderProfileCards();
});
el('scanMailboxButton').addEventListener('click', scanMailboxFromDashboard);
el('createPlanButton').addEventListener('click', createPlanFromCurrentReport);
el('recreatePlanButton').addEventListener('click', recreateCurrentPlan);
el('runCleanupButton').addEventListener('click', runCleanupSelection);
el('emptyTrashButton').addEventListener('click', emptyTrashFromDashboard);
el('deletePlanButton').addEventListener('click', deleteCurrentPlan);
el('saveConfigButton').addEventListener('click', saveConfig);
['textFilter', 'keywordFilter', 'typeFilter', 'folderFilter', 'attachmentFilter'].forEach(id => {
  el(id).addEventListener('input', render);
  el(id).addEventListener('change', render);
});
el('selectAll').addEventListener('change', (event) => {
  for (const row of visibleRows) {
    const key = rowKey(row);
    if (event.target.checked) selected.add(key);
    else selected.delete(key);
  }
  render();
});

async function scanMailboxFromDashboard() {
  const folder = el('scanMailboxSelect').value;
  const folderLabel = mailboxLabel(folder);
  const button = el('scanMailboxButton');
  const status = el('scanStatus');
  const output = el('cleanupOutput');
  button.disabled = true;
  output.style.display = 'block';
  output.textContent = authMode() === 'manual'
    ? 'Si la fenêtre Mailiz demande une connexion, connectez-vous puis revenez ici quand vous voyez votre boîte de réception. Sinon, le scan démarre directement.'
    : `Une fenêtre Mailiz va s’ouvrir. MailizClean se connecte automatiquement et scanne les ${folderLabel.toLowerCase()}. Vous pouvez minimiser la fenêtre, mais ne cliquez pas dedans.`;
  status.textContent = `Scan des ${folderLabel.toLowerCase()} en cours. Cela peut prendre plusieurs minutes.`;
  const progressTimer = startProgress(status, output);
  try {
    const res = await fetch('/api/run-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder, auth_mode: authMode() }),
    });
    const payload = await res.json();
    stopProgress(progressTimer);
    if (!res.ok || payload.error) throw new Error(payload.error || 'Erreur inconnue');
    status.textContent = `Scan ${payload.folder_label || folderLabel} terminé. Cliquez sur "Voir / mettre à jour la proposition" pour afficher les messages candidats.`;
    output.textContent = `${payload.output || 'Scan terminé.'}\n\nÉtape suivante : choisissez un scénario si besoin, puis cliquez sur "Voir / mettre à jour la proposition". Ensuite, cochez tout ou partie des lignes proposées avant de cliquer sur "Mettre en corbeille".`;
    mode = 'scan';
    el('modeSelect').value = 'scan';
    await loadReports();
    if (payload.report) {
      el('reportSelect').value = payload.report;
      await loadReport(payload.report);
    }
  } catch (error) {
    stopProgress(progressTimer);
    status.textContent = `Erreur : ${error.message}`;
    output.textContent = error.message;
  } finally {
    stopProgress(progressTimer);
    button.disabled = false;
  }
}

async function createPlanFromCurrentReport() {
  const reportName = el('reportSelect').value;
  const profileName = el('profileSelect').value;
  const before = el('beforeOverride').value;
  const includeUnread = el('includeUnreadOverride').checked;
  const planName = el('planName').value.trim();
  if (!reportName || !profileName) return;

  const button = el('createPlanButton');
  const status = el('planActionStatus');
  button.disabled = true;
  status.textContent = 'Préparation de la proposition...';
  try {
    const res = await fetch('/api/create-plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report: reportName, profile: profileName, before, include_unread: includeUnread, name: planName })
    });
    const payload = await res.json();
    if (!res.ok || payload.error) throw new Error(payload.error || 'Erreur inconnue');
    status.textContent = `Proposition prête : ${payload.candidate_count} message${payload.candidate_count > 1 ? 's' : ''}.`;
    mode = 'plan';
    el('modeSelect').value = 'plan';
    await loadReports();
    el('reportSelect').value = payload.json;
    await loadReport(payload.json);
  } catch (error) {
    status.textContent = `Erreur : ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function recreateCurrentPlan() {
  const planName = el('reportSelect').value;
  const name = el('recreatePlanName').value.trim();
  const before = el('planCutoffInput')?.value || el('recreateBeforeOverride').value;
  if (!planName) return;

  const button = el('recreatePlanButton');
  const status = el('planEditStatus');
  button.disabled = true;
  status.textContent = 'Recalcul de la proposition...';
  try {
    const res = await fetch('/api/recreate-plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan: planName, name, before })
    });
    const payload = await res.json();
    if (!res.ok || payload.error) throw new Error(payload.error || 'Erreur inconnue');
    status.textContent = `Proposition recalculée : ${payload.json} (${payload.candidate_count} candidats).`;
    await loadReports();
    el('reportSelect').value = payload.json;
    await loadReport(payload.json);
  } catch (error) {
    status.textContent = `Erreur : ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function runCleanupSelection() {
  const planName = el('reportSelect').value;
  const selectedKeys = rows.filter(row => selected.has(rowKey(row))).map(rowKey);
  if (!planName) return;
  if (!selectedKeys.length) {
    el('planEditStatus').textContent = 'Aucun message coché.';
    return;
  }
  if (!window.confirm(`Mettre ${selectedKeys.length} message${selectedKeys.length > 1 ? 's' : ''} dans la corbeille Mailiz ?\n\n${authModeText()}`)) return;

  const button = el('runCleanupButton');
  const status = el('planEditStatus');
  const output = el('cleanupOutput');
  button.disabled = true;
  output.style.display = 'block';
  output.textContent = 'Session Mailiz et mise en corbeille en cours...';
  status.textContent = 'Mise en corbeille en cours. Ne pas fermer cette fenêtre.';
  const progressTimer = startProgress(status, output);
  try {
    const res = await fetch('/api/run-cleanup-selection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan: planName, selected_keys: selectedKeys, auth_mode: authMode() })
    });
    const payload = await res.json();
    stopProgress(progressTimer);
    if (!res.ok || payload.error) throw new Error(payload.error || 'Erreur inconnue');
    status.textContent = `${payload.count} message${payload.count > 1 ? 's' : ''} mis en corbeille.`;
    output.textContent = payload.output || 'Terminé.';
    if (payload.trash_count != null) {
      el('emptyTrashButton').disabled = payload.trash_count < 1;
      el('emptyTrashButton').textContent = `Vider la corbeille (${payload.trash_count})`;
    }
  } catch (error) {
    stopProgress(progressTimer);
    status.textContent = `Erreur : ${error.message}`;
    output.textContent = error.message;
  } finally {
    stopProgress(progressTimer);
    button.disabled = false;
  }
}

async function emptyTrashFromDashboard() {
  const buttonText = el('emptyTrashButton').textContent || '';
  const countMatch = buttonText.match(new RegExp(String.raw`[(]([0-9]+)[)]`));
  const countText = countMatch ? `${countMatch[1]} message${countMatch[1] === '1' ? '' : 's'}` : 'les messages';
  if (!window.confirm(`Supprimer définitivement ${countText} actuellement dans la corbeille Mailiz ?\n\nCette action ne pourra pas être annulée.`)) return;
  const folder = el('scanMailboxSelect').value;
  const button = el('emptyTrashButton');
  const status = el('planEditStatus');
  const output = el('cleanupOutput');
  button.disabled = true;
  output.style.display = 'block';
  output.textContent = 'Session Mailiz et vidage de la corbeille en cours...';
  status.textContent = 'Vidage de la corbeille en cours.';
  const progressTimer = startProgress(status, output);
  try {
    const res = await fetch('/api/empty-trash', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder, auth_mode: authMode() }),
    });
    const payload = await res.json();
    stopProgress(progressTimer);
    if (!res.ok || payload.error) throw new Error(payload.error || 'Erreur inconnue');
    status.textContent = 'Corbeille vidée.';
    output.textContent = payload.output || 'Terminé.';
    el('emptyTrashButton').textContent = 'Vider la corbeille';
    el('emptyTrashButton').disabled = true;
    if (payload.report) {
      mode = 'scan';
      el('modeSelect').value = 'scan';
      await loadReports();
      el('reportSelect').value = payload.report;
      await loadReport(payload.report);
      el('scanStatus').textContent = `Scan de contrôle terminé : ${payload.report}.`;
    }
  } catch (error) {
    stopProgress(progressTimer);
    status.textContent = `Erreur : ${error.message}`;
    output.textContent = error.message;
  } finally {
    stopProgress(progressTimer);
    if (button.textContent.includes('(')) {
      button.disabled = false;
    }
  }
}

async function deleteCurrentPlan() {
  const planName = el('reportSelect').value;
  if (!planName) return;
  if (!window.confirm(`Retirer cette proposition de l’historique local ? Les messages Mailiz ne seront pas modifiés.`)) return;

  const button = el('deletePlanButton');
  const status = el('planEditStatus');
  button.disabled = true;
  status.textContent = 'Retrait de l’historique...';
  try {
    const res = await fetch('/api/delete-plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan: planName })
    });
    const payload = await res.json();
    if (!res.ok || payload.error) throw new Error(payload.error || 'Erreur inconnue');
    status.textContent = `Proposition retirée de l’historique : ${payload.deleted.join(', ')}.`;
    await loadReports();
  } catch (error) {
    status.textContent = `Erreur : ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

function updateProfileFields() {
  const profileName = el('profileSelect').value;
  const profile = profiles.find(item => item.name === profileName);
  if (!profile) return;
  el('beforeOverride').value = profile.before || '';
  el('includeUnreadOverride').checked = Boolean(profile.include_unread);
  if (!el('planName').value.trim()) {
    el('planName').placeholder = profile.label || profile.name;
  }
}

function updatePlanEditFields() {
  if (!report || mode !== 'plan') return;
  const input = el('recreatePlanName');
  if (document.activeElement !== input) {
    input.value = basePlanName(el('reportSelect').value || '');
  }
  const beforeInput = el('recreateBeforeOverride');
  if (document.activeElement !== beforeInput) {
    beforeInput.value = report.rules?.before || '';
  }
}

function basePlanName(name) {
  return String(name)
    .replace(/\\.json$/i, '')
    .replace(/-\\d{8}-\\d{6}$/i, '');
}

function parseSizeBytes(value) {
  const match = String(value || '').trim().match(/^([0-9]+(?:[.,][0-9]+)?)\\s*([kmgt]?o|octets?)$/i);
  if (!match) return 0;
  const amount = Number(match[1].replace(',', '.'));
  if (!Number.isFinite(amount)) return 0;
  const unit = match[2].toLowerCase();
  if (unit === 'go') return amount * 1024 * 1024 * 1024;
  if (unit === 'mo') return amount * 1024 * 1024;
  if (unit === 'ko') return amount * 1024;
  return amount;
}

function sumRowBytes(items) {
  return items.reduce((sum, row) => sum + parseSizeBytes(row.size), 0);
}

function formatBytes(bytes) {
  if (!bytes) return '0 Mo';
  if (bytes >= 1024 * 1024 * 1024) {
    return `${(bytes / 1024 / 1024 / 1024).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} Go`;
  }
  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} Mo`;
  }
  return `${(bytes / 1024).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} ko`;
}

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return '?%';
  return `${Number(value).toLocaleString('fr-FR', { maximumFractionDigits: 1 })}%`;
}

function formatStoredSize(size) {
  if (!size) return '-';
  if (typeof size.mb === 'number') return `${size.mb.toLocaleString('fr-FR')} Mo`;
  return '-';
}

renderAuthModeCards();
loadConfig();
loadProfiles().then(loadReports);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    report_dir: Path = Path("data/reports")
    rules_file: Path = DEFAULT_RULES_PATH

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.respond_text(HTML_PAGE, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/reports":
            params = parse_qs(parsed.query)
            kind = params.get("kind", ["scan"])[0]
            self.respond_json(list_reports(self.report_dir, kind=kind))
            return
        if parsed.path == "/api/report":
            params = parse_qs(parsed.query)
            name = params.get("name", [""])[0]
            try:
                self.respond_json(load_report(self.report_dir, name))
            except ValueError as exc:
                self.respond_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except FileNotFoundError:
                self.respond_json({"error": "rapport introuvable"}, HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/profiles":
            try:
                self.respond_json(list_profiles(self.rules_file))
            except (FileNotFoundError, ValueError) as exc:
                self.respond_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/config":
            self.respond_json(current_config_status())
            return
        if parsed.path == "/api/progress":
            self.respond_json(MAILIZ_SESSION.progress_snapshot())
            return

        self.respond_json({"error": "route introuvable"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {
            "/api/create-plan",
            "/api/recreate-plan",
            "/api/delete-plan",
            "/api/export-selection",
            "/api/run-cleanup-selection",
            "/api/empty-trash",
            "/api/run-scan",
            "/api/config",
        }:
            self.respond_json({"error": "route introuvable"}, HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body or "{}")
            if parsed.path == "/api/create-plan":
                self.respond_json(create_plan_from_request(self.report_dir, self.rules_file, payload))
            elif parsed.path == "/api/config":
                self.respond_json(write_user_config(payload))
            elif parsed.path == "/api/recreate-plan":
                self.respond_json(recreate_plan_from_request(self.report_dir, payload))
            elif parsed.path == "/api/delete-plan":
                self.respond_json(delete_plan_from_request(self.report_dir, payload))
            elif parsed.path == "/api/export-selection":
                self.respond_json(export_selection_from_request(self.report_dir, payload))
            elif parsed.path == "/api/run-cleanup-selection":
                self.respond_json(run_cleanup_selection_from_request(self.report_dir, payload))
            elif parsed.path == "/api/empty-trash":
                self.respond_json(empty_trash_from_dashboard(self.report_dir, payload))
            else:
                self.respond_json(run_scan_from_dashboard(self.report_dir, payload))
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            self.respond_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            logging.exception("Erreur pendant l'action dashboard")
            self.respond_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        except Exception as exc:
            logging.exception("Erreur inattendue pendant l'action dashboard")
            self.respond_json({"error": f"Erreur inattendue: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args) -> None:
        return

    def respond_text(
        self,
        body: str,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def respond_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        self.respond_text(body, "application/json; charset=utf-8", status)


def payload_kind(payload: dict, path: Path) -> str:
    if payload.get("action") == "move_to_trash" or path.name.startswith("move-selection-"):
        return "selection"
    if "rules" in payload or path.name.startswith("cleanup-plan-"):
        return "plan"
    return "scan"


def list_reports(report_dir: Path, kind: str = "scan") -> list[dict]:
    report_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for path in sorted(report_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        current_kind = payload_kind(payload, path)
        if kind != "all" and current_kind != kind:
            continue
        folders = report_folders(payload)
        reports.append(
            {
                "name": path.name,
                "kind": current_kind,
                "generated_at": payload.get("generated_at", ""),
                "count": payload.get("candidate_count", payload.get("count", 0)),
                "quota": payload.get("quota", {}),
                "folders": folders,
                "folder_label": report_folder_label(folders),
                "mtime": path.stat().st_mtime,
            }
        )
    return reports


def report_folders(payload: dict) -> list[str]:
    folders = []
    for summary in payload.get("summaries") or []:
        folder = summary.get("folder", "")
        if folder and folder not in folders:
            folders.append(folder)
    if not folders:
        for item in payload.get("items") or []:
            folder = item.get("folder", "")
            if folder and folder not in folders:
                folders.append(folder)
    return folders


def report_folder_label(folders: list[str]) -> str:
    if not folders:
        return ""
    return ", ".join(dashboard_mailbox_label(folder) for folder in folders)


def load_report(report_dir: Path, name: str) -> dict:
    if not name or "/" in name or "\\" in name or not name.endswith(".json"):
        raise ValueError("nom de rapport invalide")
    path = report_dir / name
    resolved_dir = report_dir.resolve()
    resolved_path = path.resolve()
    if resolved_dir not in resolved_path.parents and resolved_path != resolved_dir:
        raise ValueError("chemin de rapport invalide")
    return json.loads(path.read_text(encoding="utf-8"))


def list_profiles(rules_file: Path) -> list[dict]:
    profiles = load_cleanup_profiles(rules_file)
    return [
        {
            "name": name,
            "label": profile.get("label", name),
            "description": profile.get("description", ""),
            "quota_over": profile.get("quota_over"),
            "quota_target_under": profile.get("quota_target_under"),
            "before": profile.get("before", ""),
            "types": profile.get("types", []),
            "groups": profile.get("groups", []),
            "include_unread": bool(profile.get("include_unread", False)),
        }
        for name, profile in sorted(profiles.items())
        if isinstance(profile, dict)
    ]


def create_plan_from_request(report_dir: Path, rules_file: Path, payload: dict) -> dict:
    report_name = payload.get("report")
    profile_name = payload.get("profile")
    if not report_name:
        raise ValueError("rapport manquant")
    if not profile_name:
        raise ValueError("profil manquant")

    source_path = safe_report_path(report_dir, report_name)
    profiles = load_cleanup_profiles(rules_file)
    if profile_name not in profiles:
        raise ValueError(f"profil inconnu: {profile_name}")
    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        raise ValueError(f"profil invalide: {profile_name}")

    before = payload.get("before") or profile.get("before")
    include_unread = (
        bool(payload["include_unread"])
        if "include_unread" in payload
        else bool(profile.get("include_unread", False))
    )
    plan_name = sanitize_plan_name(payload.get("name") or f"cleanup-plan-{profile_name}")
    paths, count = create_cleanup_plan(
        report_path=source_path,
        output_dir=report_dir,
        prefix=plan_name,
        rules_name=profile_name,
        profile=profile,
        quota_over=profile.get("quota_over"),
        quota_target_under=profile.get("quota_target_under"),
        before=before,
        types=list(profile.get("types", [])),
        groups=list(profile.get("groups", [])),
        include_unread=include_unread,
    )
    return {
        "ok": True,
        "candidate_count": count,
        "json": paths["json"].name,
        "csv": paths["csv"].name,
    }


def recreate_plan_from_request(report_dir: Path, payload: dict) -> dict:
    plan_name = payload.get("plan")
    if not plan_name:
        raise ValueError("plan manquant")

    plan_path = safe_report_path(report_dir, plan_name)
    plan_payload = load_report(report_dir, plan_path.name)
    if payload_kind(plan_payload, plan_path) != "plan":
        raise ValueError("le fichier selectionne n'est pas un plan")

    rules = plan_payload.get("rules") or {}
    source_name = Path(plan_payload.get("source_report", "")).name
    if not source_name:
        raise ValueError("rapport source absent du plan")
    source_path = safe_report_path(report_dir, source_name)

    prefix = sanitize_plan_name(payload.get("name") or strip_timestamp(plan_path.stem))
    before = payload.get("before") or rules.get("before")
    paths, count = create_cleanup_plan(
        report_path=source_path,
        output_dir=report_dir,
        prefix=prefix,
        rules_name=rules.get("profile", ""),
        profile={
            "label": rules.get("profile_label", ""),
            "description": rules.get("profile_description", ""),
        },
        quota_over=rules.get("quota_over"),
        quota_target_under=rules.get("quota_target_under"),
        before=before,
        types=list(rules.get("types", [])),
        groups=list(rules.get("groups", [])),
        include_unread=bool(rules.get("include_unread", False)),
    )
    return {
        "ok": True,
        "candidate_count": count,
        "json": paths["json"].name,
        "csv": paths["csv"].name,
    }


def delete_plan_from_request(report_dir: Path, payload: dict) -> dict:
    plan_name = payload.get("plan")
    if not plan_name:
        raise ValueError("plan manquant")

    plan_path = safe_report_path(report_dir, plan_name)
    plan_payload = load_report(report_dir, plan_path.name)
    if payload_kind(plan_payload, plan_path) != "plan":
        raise ValueError("seuls les plans peuvent etre supprimes ici")

    deleted = []
    for path in (plan_path, plan_path.with_suffix(".csv")):
        if path.exists():
            path.unlink()
            deleted.append(path.name)
    return {"ok": True, "deleted": deleted}


def export_selection_from_request(report_dir: Path, payload: dict) -> dict:
    plan_name = payload.get("plan")
    selected_keys = set(payload.get("selected_keys") or [])
    if not plan_name:
        raise ValueError("proposition manquante")
    if not selected_keys:
        raise ValueError("aucun message coche")

    plan_path = safe_report_path(report_dir, plan_name)
    plan_payload = load_report(report_dir, plan_path.name)
    if payload_kind(plan_payload, plan_path) != "plan":
        raise ValueError("le fichier selectionne n'est pas une proposition")

    selected_items = []
    for item in plan_payload.get("items", []):
        key = f"{item.get('folder', '')}|{item.get('uid') or item.get('message_id') or item.get('subject')}"
        if key in selected_keys:
            selected_items.append(
                {
                    "folder": item.get("folder", ""),
                    "uid": item.get("uid", ""),
                    "date": item.get("date", ""),
                    "size": item.get("size", ""),
                    "document_type": item.get("document_type", ""),
                    "patient": item.get("patient", ""),
                    "sender": item.get("sender", ""),
                    "sender_email": item.get("sender_email", ""),
                    "subject": item.get("subject", ""),
                    "unread": bool(item.get("unread")),
                    "has_attachment": bool(item.get("has_attachment")),
                    "reason": item.get("reason", ""),
                }
            )

    if not selected_items:
        raise ValueError("aucun message coche ne correspond a la proposition")
    missing_uid = [item for item in selected_items if not item.get("uid")]
    if missing_uid:
        raise ValueError(f"{len(missing_uid)} message(s) coche(s) sans UID Roundcube")

    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    export_path = report_dir / f"move-selection-{timestamp}.json"
    export_payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "action": "move_to_trash",
        "target_folder": "Trash",
        "source_plan": plan_path.name,
        "source_report": plan_payload.get("source_report", ""),
        "rules": plan_payload.get("rules", {}),
        "count": len(selected_items),
        "items": selected_items,
    }
    export_path.write_text(json.dumps(export_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "json": export_path.name, "count": len(selected_items)}


def run_cleanup_selection_from_request(report_dir: Path, payload: dict) -> dict:
    auth_mode = validate_auth_mode(payload.get("auth_mode"))
    export = export_selection_from_request(report_dir, payload)
    selection_path = report_dir / export["json"]
    selection_payload = json.loads(selection_path.read_text(encoding="utf-8"))
    result = MAILIZ_SESSION.run(
        MAILIZ_SESSION.move_to_trash(selection_payload.get("items", []), export["json"], auth_mode=auth_mode)
    )
    trash_count = result["trash_status"].get("reported_count")
    output = (
        f"{len(result['moved'])} message(s) mis en corbeille.\n"
        f"{len(result['missing'])} message(s) introuvable(s).\n"
        f"Corbeille Trash: {trash_count if trash_count is not None else result['trash_status'].get('scanned_count', 0)} message(s)."
    )
    return {
        "ok": True,
        "selection": export["json"],
        "count": len(result["moved"]),
        "trash_count": trash_count,
        "output": output,
    }


def empty_trash_from_dashboard(report_dir: Path, payload: dict | None = None) -> dict:
    folder = validate_dashboard_mailbox((payload or {}).get("folder"))
    auth_mode = validate_auth_mode((payload or {}).get("auth_mode"))
    result = MAILIZ_SESSION.run(MAILIZ_SESSION.empty_trash(report_dir, folders=[folder], auth_mode=auth_mode))
    before_count = result["before"].get("reported_count")
    after_count = result["after"].get("reported_count")
    lines = [
        f"Corbeille avant: {before_count if before_count is not None else result['before'].get('scanned_count', 0)} message(s).",
        f"Corbeille apres: {after_count if after_count is not None else result['after'].get('scanned_count', 0)} message(s).",
        f"Quota avant vidage: {result.get('quota_before') or 'non detecte'}",
        f"Quota apres vidage: {result.get('quota_after') or 'non detecte'}",
    ]
    post_scan = result.get("post_scan") or {}
    if post_scan:
        lines.append(f"Scan de controle: {post_scan.get('json')} ({post_scan.get('count')} message(s)).")
    return {
        "ok": True,
        "output": "\n".join(lines),
        "report": post_scan.get("json", ""),
        "folder": folder,
        "folder_label": dashboard_mailbox_label(folder),
    }


def run_scan_from_dashboard(report_dir: Path, payload: dict | None = None) -> dict:
    folder = validate_dashboard_mailbox((payload or {}).get("folder"))
    auth_mode = validate_auth_mode((payload or {}).get("auth_mode"))
    result = MAILIZ_SESSION.run(MAILIZ_SESSION.scan_mailbox(report_dir, folders=[folder], auth_mode=auth_mode))
    output = (
        f"Scan {result.get('folder_label') or dashboard_mailbox_label(folder)} termine: {result['count']} message(s).\n"
        f"Rapport: {result['json']}\n"
        f"Quota: {result.get('quota') or 'non detecte'}"
    )
    return {
        "ok": True,
        "report": result["json"],
        "output": output,
        "folder": folder,
        "folder_label": result.get("folder_label") or dashboard_mailbox_label(folder),
    }


def sanitize_plan_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._")
    return clean or "cleanup-plan"


def strip_timestamp(value: str) -> str:
    return re.sub(r"-\d{8}-\d{6}$", "", value)


def safe_report_path(report_dir: Path, name: str) -> Path:
    if not name or "/" in name or "\\" in name or not name.endswith(".json"):
        raise ValueError("nom de rapport invalide")
    path = report_dir / name
    resolved_dir = report_dir.resolve()
    resolved_path = path.resolve()
    if resolved_dir not in resolved_path.parents:
        raise ValueError("chemin de rapport invalide")
    if not resolved_path.exists():
        raise FileNotFoundError(name)
    return resolved_path


def run_dashboard(host: str, port: int, report_dir: Path) -> int:
    handler = type(
        "ConfiguredDashboardHandler",
        (DashboardHandler,),
        {"report_dir": report_dir, "rules_file": DEFAULT_RULES_PATH},
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Dashboard MailizClean: http://{host}:{port}")
    print("Session Mailiz conservee pendant que le dashboard reste lance. Ctrl+C pour arreter.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
