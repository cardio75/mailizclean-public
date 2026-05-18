from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import email
import imaplib
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from config.settings import FOLDERS, SECRETS, TIMEOUTS, validate_required_secrets


MAILIZ_URL = "https://mailiz.mssante.fr/"
DEBUG_DIR = FOLDERS["TEMP"] / "debug"
DEFAULT_RULES_PATH = Path(__file__).parent / "config" / "cleanup_rules.json"
DEFAULT_KEYWORDS = ("hnet", "XDM")
DEFAULT_FOLDERS = ("INBOX",)
DEFAULT_TRASH_FOLDER = "Trash"
MOVE_BATCH_SIZE = 50
TRASH_CONFIRMATION = "VIDER LA CORBEILLE"
MOVE_CONFIRMATION = "DEPLACER VERS CORBEILLE"
OTHER_KEYWORD = "autres"
KEYWORD_TYPES = {
    "hnet": "biologie",
    "xdm": "compte-rendu",
    OTHER_KEYWORD: "autre",
}


class MailizAccessBlockedError(RuntimeError):
    pass


@dataclass
class QuotaInfo:
    raw: str = ""
    used: str = ""
    total: str = ""
    percent: int | None = None


@dataclass
class PageCount:
    raw: str = ""
    start: int | None = None
    end: int | None = None
    total: int | None = None
    empty: bool = False


@dataclass
class ScanItem:
    scanned_at: str
    folder: str
    keyword: str
    matched_keywords: str
    probable_type: str
    document_type: str
    date: str
    size: str
    sender: str
    sender_email: str
    subject: str
    patient: str
    has_attachment: bool
    unread: bool
    uid: str
    message_id: str
    row_text: str


@dataclass
class ScanSummary:
    folder: str
    keyword: str
    pages_scanned: int
    last_count: PageCount


@dataclass
class CleanupPlanItem:
    selected: bool
    reason: str
    folder: str
    uid: str
    date: str
    size: str
    matched_keywords: str
    document_type: str
    patient: str
    sender: str
    sender_email: str
    subject: str
    unread: bool
    has_attachment: bool


@dataclass
class SizeInfo:
    raw: str = ""
    bytes: int = 0
    mb: float = 0.0


def setup_logging(verbose: bool = False) -> None:
    log_dir = FOLDERS["LOGS"]
    log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "mailiz_cleaner.log"),
            logging.StreamHandler(),
        ],
    )


def clean_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator="\n").strip()


def extract_otp_from_email(email_body: str) -> str | None:
    patterns = [
        r"code d['’]acc[eè]s [aà] usage\s+unique\s*:\s*(\d{6})",
        r"\bOTP\b\s*:?\s*(\d{6})",
        r"\bcode\b[^\d]{0,30}(\d{6})",
    ]
    for pattern in patterns:
        match = re.search(pattern, email_body, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def decode_message_body(msg: email.message.Message) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    fallback = ""

    for part in parts:
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        if content_type == "text/plain":
            return text
        fallback = clean_html(text)

    return fallback


async def get_otp_code(delete_otp_email: bool = False) -> str | None:
    imap_server = None
    try:
        imap_server = imaplib.IMAP4_SSL(SECRETS["otp_email"]["imap_server"])
        imap_server.login(SECRETS["otp_email"]["email"], SECRETS["otp_email"]["password"])
        imap_server.select("INBOX")

        status, messages = imap_server.search(None, '(UNSEEN FROM "Mailiz Keycloak")')
        if status != "OK":
            logging.error("Erreur lors de la recherche des emails OTP")
            return None

        mail_ids = messages[0].split()
        if not mail_ids:
            logging.error("Aucun mail OTP non lu trouve")
            return None

        for mail_id in reversed(mail_ids):
            status, msg_data = imap_server.fetch(mail_id, "(RFC822)")
            if status != "OK":
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            date_tuple = email.utils.parsedate_tz(msg["Date"])
            if date_tuple:
                msg_time = dt.datetime.fromtimestamp(email.utils.mktime_tz(date_tuple))
                if (dt.datetime.now() - msg_time) > dt.timedelta(minutes=90):
                    continue

            otp = extract_otp_from_email(decode_message_body(msg))
            if not otp:
                continue

            if delete_otp_email:
                imap_server.store(mail_id, "+FLAGS", "\\Deleted")
                imap_server.expunge()
                logging.info("Email OTP supprime apres recuperation du code")

            return otp

        return None
    except Exception as exc:
        logging.error("Erreur lors de la recuperation de l'OTP: %s", exc)
        return None
    finally:
        if imap_server:
            try:
                imap_server.close()
                imap_server.logout()
            except Exception:
                pass


async def login_mailiz(page, delete_otp_email: bool = False) -> None:
    logging.info("Connexion a Mailiz")
    await page.goto(MAILIZ_URL, wait_until="domcontentloaded")
    await assert_mailiz_page_not_blocked(page)
    await click_first_available(
        page,
        [
            "#connexionWebmail",
            "a#connexionWebmail",
            "button#connexionWebmail",
            "a:has-text('Me connecter')",
            "button:has-text('Me connecter')",
            "[role='link']:has-text('Me connecter')",
        ],
        "bouton de connexion Mailiz",
    )

    logging.info("Saisie des identifiants")
    await fill_first_available(
        page,
        [
            "input[name='username']",
            "input[name='email']",
            "input[type='email']",
            "input#username",
            "input#email",
            "input[autocomplete='username']",
            "input[placeholder*='Email']",
        ],
        SECRETS["mailiz"]["user"],
        "champ email Mailiz",
    )
    await fill_first_available(
        page,
        [
            "input[type='password']",
            "input[name='password']",
            "input#password",
            "input[autocomplete='current-password']",
        ],
        SECRETS["mailiz"]["password"],
        "champ mot de passe Mailiz",
    )
    await click_first_available(
        page,
        [
            "button:has-text('Me Connecter')",
            "button:has-text('Me connecter')",
            "input[type='submit']",
            "button[type='submit']",
        ],
        "bouton de soumission identifiants",
    )

    logging.info("Selection de l'authentification par mail")
    await page.get_by_text(re.compile("Par mail", re.I)).click()
    await click_first_available(
        page,
        ["button:has-text('Soumettre')", "input[type='submit']", "button[type='submit']"],
        "bouton soumettre methode OTP",
    )

    logging.info("Attente du mail OTP")
    await asyncio.sleep(30)
    otp_code = await get_otp_code(delete_otp_email=delete_otp_email)
    if not otp_code:
        raise RuntimeError("Code OTP non recu")

    await fill_first_available(
        page,
        [
            "input[name='otp']",
            "input[name*='otp' i]",
            "input[aria-label*='OTP' i]",
            "input[placeholder*='OTP' i]",
            "input[type='text']",
        ],
        otp_code,
        "champ OTP",
    )
    await click_first_available(
        page,
        ["button:has-text('Soumettre')", "input[type='submit']", "button[type='submit']"],
        "bouton soumettre OTP",
    )
    await page.wait_for_load_state("networkidle")
    await page.locator("#layout-sidebar, #mailboxlist").first.wait_for(
        timeout=TIMEOUTS["element_wait"]
    )
    logging.info("Connexion reussie")


async def click_first_available(page, selectors: list[str], label: str) -> None:
    await assert_mailiz_page_not_blocked(page)
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=5000)
            await locator.click()
            logging.debug("%s clique via %s", label, selector)
            return
        except PlaywrightTimeoutError:
            continue

    await assert_mailiz_page_not_blocked(page)
    await save_debug_artifacts(page, f"missing-{slugify(label)}")
    raise RuntimeError(f"Element introuvable: {label}")


async def fill_first_available(page, selectors: list[str], value: str, label: str) -> None:
    await assert_mailiz_page_not_blocked(page)
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=5000)
            await locator.fill(value)
            logging.debug("%s rempli via %s", label, selector)
            return
        except PlaywrightTimeoutError:
            continue

    await assert_mailiz_page_not_blocked(page)
    await save_debug_artifacts(page, f"missing-{slugify(label)}")
    raise RuntimeError(f"Element introuvable: {label}")


async def assert_mailiz_page_not_blocked(page) -> None:
    content = await page.content()
    if (
        "Web Page Blocked" not in content
        and "The URL you requested has been blocked" not in content
        and "Attack ID:" not in content
    ):
        return
    await save_debug_artifacts(page, "mailiz-page-bloquee", log_level=logging.INFO)
    attack_match = re.search(r"Attack ID:\s*([0-9]+)", content)
    message_match = re.search(r"Message ID:\s*([0-9]+)", content)
    details = []
    if attack_match:
        details.append(f"Attack ID {attack_match.group(1)}")
    if message_match:
        details.append(f"Message ID {message_match.group(1)}")
    suffix = f" ({', '.join(details)})" if details else ""
    raise MailizAccessBlockedError(f"Mailiz a bloque Chromium avant la connexion{suffix}")


async def save_debug_artifacts(page, prefix: str, log_level: int = logging.ERROR) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    html_path = DEBUG_DIR / f"{prefix}-{timestamp}.html"
    png_path = DEBUG_DIR / f"{prefix}-{timestamp}.png"
    try:
        html_path.write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=png_path, full_page=True)
        logging.log(log_level, "Artefacts debug ecrits: %s et %s", html_path, png_path)
    except Exception as exc:
        logging.error("Impossible d'ecrire les artefacts debug: %s", exc)


async def save_inspection_artifacts(page, prefix: str) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    html_path = DEBUG_DIR / f"{prefix}-{timestamp}.html"
    png_path = DEBUG_DIR / f"{prefix}-{timestamp}.png"
    try:
        html_path.write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=png_path, full_page=True)
        logging.info("Artefacts inspection ecrits: %s et %s", html_path, png_path)
    except Exception as exc:
        logging.warning("Impossible d'ecrire les artefacts inspection: %s", exc)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


async def get_mailbox_quota(page) -> QuotaInfo:
    quota = page.locator("#layout-sidebar #quotadisplay, #quotadisplay").first
    try:
        await quota.wait_for(timeout=TIMEOUTS["element_wait"])
    except PlaywrightTimeoutError:
        return QuotaInfo()

    raw = (
        await quota.get_attribute("data-original-title")
        or await page.locator("#rcmquotadisplay").first.get_attribute("title")
        or await quota.inner_text()
    )
    raw = normalize_space(raw)

    percent_match = re.search(r"\((\d+)\s*%\)|\b(\d+)\s*%", raw)
    usage_match = re.search(
        r"([\d,.]+)\s*([GMK]o)\s*/\s*([\d,.]+)\s*([GMK]o)",
        raw,
        re.IGNORECASE,
    )

    percent = None
    if percent_match:
        percent = int(percent_match.group(1) or percent_match.group(2))

    used = ""
    total = ""
    if usage_match:
        used = f"{usage_match.group(1).replace(',', '.')} {usage_match.group(2)}"
        total = f"{usage_match.group(3).replace(',', '.')} {usage_match.group(4)}"

    return QuotaInfo(raw=raw, used=used, total=total, percent=percent)


async def open_folder(page, folder: str) -> None:
    folder_link = page.locator(folder_link_selector(folder)).first
    try:
        await folder_link.wait_for(timeout=TIMEOUTS["element_wait"])
    except PlaywrightTimeoutError:
        await save_debug_artifacts(page, f"missing-folder-{slugify(folder)}")
        raise RuntimeError(f"Dossier Roundcube introuvable: {folder}")
    await folder_link.click()
    await page.wait_for_load_state("networkidle")


def folder_link_selector(folder: str) -> str:
    return (
        f'#mailboxlist a[rel="{folder}"], '
        f'#rcm_folderlist a[rel="{folder}"], '
        f'#folderlist a[rel="{folder}"]'
    )


async def list_roundcube_folders(page) -> list[dict]:
    return await page.evaluate(
        """() => Array.from(document.querySelectorAll(
            '#mailboxlist a[rel], #rcm_folderlist a[rel], #folderlist a[rel]'
        )).map((link) => ({
            rel: link.getAttribute('rel') || '',
            text: (link.textContent || '').replace(/\\s+/g, ' ').trim(),
            id: link.id || '',
            className: link.className || '',
        }))"""
    )


def find_trash_folder(folders: list[dict], requested: str) -> str:
    rels = {folder.get("rel", "") for folder in folders}
    if requested in rels:
        return requested

    patterns = ("trash", "corbeille", "deleted", "supprim")
    for folder in folders:
        haystack = " ".join(
            [
                folder.get("rel", ""),
                folder.get("text", ""),
                folder.get("className", ""),
            ]
        ).casefold()
        if any(pattern in haystack for pattern in patterns):
            return folder.get("rel", "") or requested

    return requested


def log_roundcube_folders(folders: list[dict]) -> None:
    if not folders:
        logging.debug("Aucun dossier Roundcube detecte dans la colonne de gauche")
        return
    logging.debug("Dossiers Roundcube detectes:")
    for folder in folders:
        logging.debug(
            "  rel=%s text=%s class=%s",
            folder.get("rel", ""),
            folder.get("text", ""),
            folder.get("className", ""),
        )


async def run_search(page, keyword: str) -> None:
    logging.info("Recherche Mailiz: %s", keyword)
    search_box = page.get_by_role("textbox", name=re.compile("Termes de recherche", re.I))
    if not await search_box.count():
        search_box = page.locator(
            '#mailsearchform, input[name="_q"], input[type="search"], #quicksearchbox, .searchbar input'
        ).first

    await reset_roundcube_search(page)
    previous_count = await current_count_text(page)
    previous_first_row = await current_first_row_id(page)

    await set_search_value(search_box, keyword)
    await submit_roundcube_search(page, search_box)

    await page.wait_for_load_state("networkidle")
    await wait_for_search_results(page, previous_count, previous_first_row)
    await warn_if_search_probably_unfiltered(page, keyword, previous_count, previous_first_row)
    await page.wait_for_timeout(1500)


async def show_unfiltered_folder(page) -> None:
    logging.info("Scan Mailiz sans filtre de recherche")
    await reset_roundcube_search(page)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1000)


async def reset_roundcube_search(page) -> None:
    result = await page.evaluate(
        """() => {
            if (window.rcmail && typeof window.rcmail.command === 'function') {
                window.rcmail.command('reset-search');
                return true;
            }
            return false;
        }"""
    )
    if result:
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(500)


async def set_search_value(search_box, keyword: str) -> None:
    await search_box.wait_for(state="attached", timeout=TIMEOUTS["element_wait"])
    await search_box.evaluate(
        """(element, value) => {
            element.value = value;
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        keyword,
    )


async def submit_roundcube_search(page, search_box) -> None:
    result = await page.evaluate(
        """() => {
            if (window.rcmail && typeof window.rcmail.command === 'function') {
                window.rcmail.command('search');
                return true;
            }
            const form = document.forms.rcmqsearchform;
            if (form) {
                form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                return true;
            }
            return false;
        }"""
    )
    if result:
        return

    search_button = page.locator("#rcmbtn115, .searchbar .button.search").first
    if await search_button.count() and await search_button.is_visible():
        await search_button.click()
        return

    try:
        await search_box.press("Enter")
        return
    except Exception as exc:
        logging.debug("Soumission recherche par Enter impossible: %s", exc)
        await save_debug_artifacts(page, "search-submit-failed")
        raise RuntimeError("Impossible de lancer la recherche Roundcube")


async def wait_for_search_results(
    page,
    previous_count: str,
    previous_first_row: str,
) -> None:
    try:
        await page.wait_for_function(
            """([oldCount, oldFirstRow]) => {
                const count = document.querySelector('#rcmcountdisplay')?.textContent?.trim() || '';
                const firstRow = document.querySelector('#messagelist tbody tr')?.id || '';
                const hasEmpty = !!document.querySelector('#messagelist-content .listing-info');
                return (count && count !== oldCount) || (firstRow && firstRow !== oldFirstRow) || hasEmpty;
            }""",
                arg=[previous_count, previous_first_row],
            timeout=TIMEOUTS["element_wait"],
        )
    except PlaywrightTimeoutError:
        logging.warning("La recherche n'a pas signale de changement visible dans le delai imparti")


async def warn_if_search_probably_unfiltered(
    page,
    keyword: str,
    previous_count: str,
    previous_first_row: str,
) -> None:
    new_count = await current_count_text(page)
    new_first_row = await current_first_row_id(page)
    if new_count == previous_count and new_first_row == previous_first_row:
        logging.warning(
            "La recherche '%s' semble ne pas avoir filtre la liste (%s)",
            keyword,
            new_count or "compteur indisponible",
        )


async def current_count_text(page) -> str:
    count_display = page.locator("#rcmcountdisplay").first
    if await count_display.count():
        return normalize_space(await count_display.inner_text())
    return ""


async def current_first_row_id(page) -> str:
    first_row = page.locator("#messagelist tbody tr").first
    if await first_row.count():
        return normalize_space(await first_row.get_attribute("id") or "")
    return ""


async def collect_visible_rows(
    page,
    folder: str,
    keyword: str,
    filter_keyword: bool = True,
) -> list[ScanItem]:
    await page.wait_for_timeout(500)
    rows = page.locator(
        "#messagelist tbody tr, table.messagelist tbody tr, tr.message, li.message"
    )
    count = await rows.count()
    scanned_at = dt.datetime.now().isoformat(timespec="seconds")
    items: list[ScanItem] = []

    for index in range(count):
        row = rows.nth(index)
        if not await row.is_visible():
            continue

        row_text = normalize_space(await row.inner_text())
        if not row_text:
            continue

        message_id = await row.get_attribute("id") or ""
        class_name = await row.get_attribute("class") or ""
        subject = await read_first_text(
            row,
            [
                "td.subject > span.subject a span",
                "td.subject > span.subject a",
                "[data-title='Sujet']",
            ],
        )
        sender = await read_first_text(
            row,
            [
                "td.subject > span.fromto .rcmContactAddress",
                "td.subject > span.fromto",
                "td.fromto",
                "td.from",
                "[data-title='De']",
            ],
        )
        sender_email = await read_first_attr(
            row,
            [
                "td.subject > span.fromto .rcmContactAddress",
                "td.subject > span.fromto [title]",
            ],
            "title",
        )
        date = await read_first_text(
            row,
            ["td.subject > span.date", "td.date", "[data-title='Date']"],
        )
        size = await read_first_text(
            row,
            ["td.subject > span.size", "td.size", "[data-title='Taille']"],
        )
        if not size:
            size = infer_size_from_row_text(row_text)

        if not subject:
            subject = infer_subject_from_row_text(row_text)

        searchable_text = " ".join([subject, sender, sender_email, row_text])
        if filter_keyword and not row_matches_keyword(searchable_text, keyword):
            logging.debug(
                "Ligne ignoree hors mot-cle %s: uid=%s sender_domain=%s",
                keyword,
                extract_uid(await read_first_attr(row, ["td.subject > span.subject a"], "href")),
                email_domain(sender_email),
            )
            continue
        matched_keywords = detect_matched_keywords(searchable_text, keyword)
        effective_keyword = matched_keywords if not filter_keyword else keyword

        href = await read_first_attr(row, ["td.subject > span.subject a"], "href")
        items.append(
            ScanItem(
                scanned_at=scanned_at,
                folder=folder,
                keyword=effective_keyword,
                matched_keywords=matched_keywords,
                probable_type=classify_keyword(effective_keyword),
                document_type=classify_document_type(subject or row_text, effective_keyword),
                date=date,
                size=size,
                sender=sender,
                sender_email=sender_email,
                subject=subject,
                patient=extract_patient(subject or row_text),
                has_attachment=("attachment" in class_name.lower())
                or await row.locator("td.flags span.attachment span.attachment").count() > 0,
                unread="unread" in class_name.split(),
                uid=extract_uid(href),
                message_id=message_id,
                row_text=row_text,
            )
        )

    return deduplicate_items(items)


async def read_first_text(locator, selectors: list[str]) -> str:
    for selector in selectors:
        candidate = locator.locator(selector).first
        if await candidate.count():
            text = normalize_space(await candidate.inner_text())
            if text:
                return text
    return ""


async def read_first_attr(locator, selectors: list[str], attr: str) -> str:
    for selector in selectors:
        candidate = locator.locator(selector).first
        if await candidate.count():
            value = normalize_space(await candidate.get_attribute(attr))
            if value:
                return value
    return ""


def normalize_space(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def classify_keyword(keyword: str) -> str:
    values = {value.strip().casefold() for value in keyword.split("+") if value.strip()}
    if "hnet" in values:
        return "biologie"
    if "xdm" in values:
        return "compte-rendu"
    return KEYWORD_TYPES.get(keyword.lower(), "inconnu")


def row_matches_keyword(text: str, keyword: str) -> bool:
    if not keyword or keyword == OTHER_KEYWORD:
        return True
    return keyword.casefold() in normalize_space(text).casefold()


def detect_matched_keywords(text: str, scan_keyword: str) -> str:
    normalized = normalize_space(text).casefold()
    matches = [
        keyword
        for keyword in DEFAULT_KEYWORDS
        if keyword.casefold() in normalized
    ]
    if scan_keyword and scan_keyword != OTHER_KEYWORD and scan_keyword not in matches:
        matches.append(scan_keyword)
    return "+".join(matches) if matches else OTHER_KEYWORD


def classify_document_type(text: str, keyword: str = "") -> str:
    lowered = normalize_space(text).lower()
    if "examen" in lowered and "biologique" in lowered:
        return "biologie"
    if "hnet" in lowered:
        return "biologie"
    if "imagerie" in lowered or "score calcique" in lowered:
        return "imagerie"
    if "synthese d'episode" in lowered or "synthèse d'épisode" in lowered:
        return "synthese-sejour"
    if "consultation" in lowered or "visite" in lowered:
        return "consultation"
    if "lettre" in lowered:
        return "lettre"
    return classify_keyword(keyword)


def infer_subject_from_row_text(row_text: str) -> str:
    parts = [part.strip() for part in re.split(r"\s{2,}|\t", row_text) if part.strip()]
    if len(parts) >= 2:
        return parts[-1]
    return row_text[:180]


def infer_size_from_row_text(row_text: str) -> str:
    matches = re.findall(r"\b\d+(?:[,.]\d+)?\s*(?:[KMGT]?o|octets?)\b", row_text, re.IGNORECASE)
    return normalize_space(matches[-1]) if matches else ""


def extract_patient(text: str) -> str:
    cleaned = normalize_space(text)

    letter_match = re.search(
        r"Document\s+LETTRE\s+de\s+(.+?)\s+\(\d{2}\s+\d{2}\s+\d{4}\)",
        cleaned,
        re.IGNORECASE,
    )
    if letter_match:
        return normalize_space(letter_match.group(1))

    without_prefix = re.sub(r"^.*?\+", "", cleaned)
    without_prefix = re.sub(r"^(?:XDM/\S+\+)+", "", without_prefix, flags=re.IGNORECASE)
    without_prefix = re.sub(
        r"^(?:CR d['’]examens biologiques|CR d['’]imagerie medicale|CR d['’]imagerie médicale|"
        r"Synth[eè]se d['’][eé]pisode de soins|CR ou fiche de consultation ou de visite|"
        r"2 documents|Document [A-Z]+ de)[\s-]+",
        "",
        without_prefix,
        flags=re.IGNORECASE,
    )
    without_prefix = re.sub(
        r"^(?:SCORE CALCIQUE|[A-ZÀ-Ÿ ',-]+-)\s+",
        "",
        without_prefix,
        flags=re.IGNORECASE,
    )
    without_date = re.sub(
        r"\s+(?:\d{2}[/-]\d{2}[/-]\d{4}|\(\d{2}\s+\d{2}\s+\d{4}\))\s*$",
        "",
        without_prefix,
    )
    if without_date and without_date != cleaned:
        return normalize_space(without_date)

    patterns = [
        r"\b(?:PATIENT|Patient)\s*[:=-]\s*([A-Z][A-Z '\-]+ [A-Z][A-Za-zÀ-ÿ'\-]+)",
        r"\b([A-Z][A-Z '\-]{2,}\s+[A-Z][A-Za-zÀ-ÿ'\-]{2,})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return normalize_space(match.group(1))
    return ""


def extract_uid(href: str) -> str:
    match = re.search(r"[?&]_uid=(\d+)", href or "")
    return match.group(1) if match else ""


def email_domain(value: str) -> str:
    match = re.search(r"@([^>\s]+)", value or "")
    return match.group(1).lower() if match else ""


def deduplicate_items(items: list[ScanItem]) -> list[ScanItem]:
    by_uid: dict[tuple[str, str], ScanItem] = {}
    seen = set()
    unique = []
    for item in items:
        if item.uid:
            uid_key = (item.folder, item.uid)
            existing = by_uid.get(uid_key)
            if existing:
                existing.matched_keywords = merge_keywords(
                    existing.matched_keywords,
                    item.matched_keywords or item.keyword,
                )
                if existing.keyword != item.keyword:
                    existing.keyword = existing.matched_keywords
                if existing.probable_type == "autre" and item.probable_type != "autre":
                    existing.probable_type = item.probable_type
                if existing.document_type == "autre" and item.document_type != "autre":
                    existing.document_type = item.document_type
                continue
            by_uid[uid_key] = item

        key = (item.folder, item.keyword, item.uid or item.message_id or item.row_text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def merge_keywords(left: str, right: str) -> str:
    values = []
    for raw in (left, right):
        for value in raw.split("+"):
            value = value.strip()
            if value and value not in values:
                values.append(value)
    return "+".join(values)


def apply_local_filters(items: list[ScanItem], before: str | None) -> list[ScanItem]:
    if not before:
        return items

    before_date = dt.date.fromisoformat(before)
    filtered = []
    for item in items:
        parsed_date = parse_mailiz_date(item.date)
        if parsed_date and parsed_date < before_date:
            filtered.append(item)
    return filtered


def filter_items_by_keywords(items: list[ScanItem], keywords: list[str] | None) -> list[ScanItem]:
    filters = [keyword for keyword in keywords or [] if keyword]
    if not filters:
        return items

    filtered = []
    for item in items:
        searchable_text = " ".join(
            [
                item.keyword,
                item.matched_keywords,
                item.subject,
                item.sender,
                item.sender_email,
                item.row_text,
            ]
        )
        if any(row_matches_keyword(searchable_text, keyword) for keyword in filters):
            filtered.append(item)
    return filtered


def parse_mailiz_date(value: str) -> dt.date | None:
    value = normalize_space(value).lower()
    if not value:
        return None

    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass

    if re.match(r"^\d{1,2}/\d{1,2}$", value):
        day, month = value.split("/")
        return dt.date(dt.date.today().year, int(month), int(day))

    return None


async def has_next_page(page) -> bool:
    next_button = page.locator(".pagenav .nextpage").first
    if not await next_button.count():
        return False

    aria_disabled = await next_button.get_attribute("aria-disabled")
    classes = await next_button.get_attribute("class") or ""
    return aria_disabled != "true" and "disabled" not in classes.split()


async def go_next_page(page) -> bool:
    if not await has_next_page(page):
        return False

    count_display = page.locator("#rcmcountdisplay").first
    before = ""
    if await count_display.count():
        before = normalize_space(await count_display.inner_text())

    await page.locator(".pagenav .nextpage").first.click()
    if before:
        try:
            await page.wait_for_function(
                """previous => {
                    const el = document.querySelector('#rcmcountdisplay');
                    return el && el.textContent.trim() !== previous;
                }""",
                arg=before,
                timeout=TIMEOUTS["element_wait"],
            )
        except PlaywrightTimeoutError:
            logging.warning("Le compteur de pagination n'a pas change apres clic suivant")

    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(800)
    return True


async def get_page_count(page) -> PageCount:
    count_display = page.locator("#rcmcountdisplay").first
    if not await count_display.count():
        return PageCount()

    raw = normalize_space(await count_display.inner_text())
    if not raw:
        return PageCount()
    return parse_page_count(raw)


def parse_page_count(raw: str) -> PageCount:
    raw = normalize_space(raw)
    if not raw:
        return PageCount()
    if "vide" in raw.lower():
        return PageCount(raw=raw, empty=True)

    match = re.search(r"(\d+)\s+[aà]\s+(\d+)\s+sur\s+(\d+)", raw, re.IGNORECASE)
    if not match:
        return PageCount(raw=raw)

    return PageCount(
        raw=raw,
        start=int(match.group(1)),
        end=int(match.group(2)),
        total=int(match.group(3)),
        empty=False,
    )


async def collect_paginated_results(
    page,
    folder: str,
    keyword: str,
    max_pages: int,
    filter_keyword: bool = True,
    progress_callback=None,
) -> tuple[list[ScanItem], ScanSummary]:
    items: list[ScanItem] = []
    page_index = 1
    page_count = PageCount()

    while True:
        page_count = await get_page_count(page)
        scope = "boite" if keyword == OTHER_KEYWORD and not filter_keyword else keyword
        logging.info("Collecte page %s pour %s %s", page_index, scope, page_count.raw)
        if progress_callback:
            progress_callback(folder, scope, page_index, page_count)

        items.extend(await collect_visible_rows(page, folder, keyword, filter_keyword))

        if max_pages and page_index >= max_pages:
            logging.info("Limite de pagination atteinte: %s page(s)", max_pages)
            break
        if not await go_next_page(page):
            break
        page_index += 1

    return deduplicate_items(items), ScanSummary(
        folder=folder,
        keyword=keyword,
        pages_scanned=page_index,
        last_count=page_count,
    )


def summarize_scan_item_sizes(items: list[ScanItem]) -> dict:
    total_bytes = sum(parse_size(item.size).bytes for item in items)
    return {
        "bytes": total_bytes,
        "mb": round(total_bytes / 1024**2, 2),
        "gb": round(total_bytes / 1024**3, 3),
    }


async def get_trash_status(page, folder: str, max_pages: int, debug: bool = False) -> dict:
    folders = await list_roundcube_folders(page)
    if debug:
        log_roundcube_folders(folders)

    resolved_folder = find_trash_folder(folders, folder)
    if resolved_folder != folder:
        logging.info("Dossier corbeille detecte: %s au lieu de %s", resolved_folder, folder)

    logging.info("Ouverture de la corbeille %s", resolved_folder)
    await open_folder(page, resolved_folder)
    await show_unfiltered_folder(page)
    items, summary = await collect_paginated_results(
        page,
        resolved_folder,
        OTHER_KEYWORD,
        max_pages,
        filter_keyword=False,
    )
    reported_total = summary.last_count.total
    return {
        "folder": resolved_folder,
        "requested_folder": folder,
        "empty": summary.last_count.empty or reported_total == 0,
        "reported_count": reported_total,
        "scanned_count": len(items),
        "pages_scanned": summary.pages_scanned,
        "last_count": asdict(summary.last_count),
        "size": summarize_scan_item_sizes(items),
        "folders": folders if debug else [],
    }


async def purge_trash(page, folder: str) -> None:
    if folder != DEFAULT_TRASH_FOLDER:
        raise RuntimeError(f"Vidage refuse: le dossier resolu n'est pas {DEFAULT_TRASH_FOLDER} ({folder})")

    if await confirm_visible_delete_dialog(page):
        return

    folder_link = page.locator(folder_link_selector(folder)).first
    await folder_link.wait_for(timeout=TIMEOUTS["element_wait"])
    await folder_link.click(button="right")
    if not await confirm_visible_delete_dialog(page):
        await click_purge_action(page)

    await confirm_delete_dialog(page)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)


async def confirm_visible_delete_dialog(page) -> bool:
    confirm = page.locator(".ui-dialog .mainaction.delete").first
    if await confirm.count() and await confirm.is_visible():
        logging.debug("Dialogue de confirmation deja ouvert")
        await confirm.click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        return True
    return False


async def confirm_delete_dialog(page) -> None:
    dialog = page.locator(".ui-dialog").first
    try:
        await dialog.wait_for(state="visible", timeout=5000)
        await page.locator(".ui-dialog .mainaction.delete").first.click()
    except PlaywrightTimeoutError:
        await save_debug_artifacts(page, "trash-purge-confirm-missing")
        raise RuntimeError("Confirmation Roundcube de vidage de corbeille introuvable")


async def click_purge_action(page) -> None:
    try:
        await page.locator("#rcm_folderlist, .contextmenu, .popover").first.wait_for(
            state="visible",
            timeout=TIMEOUTS["element_wait"],
        )
    except PlaywrightTimeoutError:
        logging.warning("Menu contextuel Roundcube non detecte apres clic droit")

    candidates = await page.evaluate(
        """() => Array.from(document.querySelectorAll('.cmd_purge')).map((el) => ({
            text: (el.textContent || '').replace(/\\s+/g, ' ').trim(),
            className: el.className || '',
            visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
        }))"""
    )
    logging.debug("Actions purge detectees: %s", candidates)

    purge_selectors = [
        "#rcm_folderlist .cmd_purge.active",
        "#rcm_folderlist .cmd_purge",
        ".contextmenu .cmd_purge.active",
        ".contextmenu .cmd_purge",
        ".popover .cmd_purge.active",
        ".popover .cmd_purge",
        ".cmd_purge.active",
        ".cmd_purge",
    ]
    for selector in purge_selectors:
        purge_action = page.locator(selector).first
        try:
            await purge_action.wait_for(state="attached", timeout=1500)
            await purge_action.click(force=True, timeout=1500)
            logging.debug("Action vider cliquee via %s", selector)
            return
        except PlaywrightTimeoutError:
            continue
        except Exception as exc:
            logging.debug("Clic action vider impossible via %s: %s", selector, exc)

    try:
        await page.get_by_text("Vider", exact=True).click(force=True, timeout=1500)
        logging.debug("Action vider cliquee via texte")
        return
    except Exception as exc:
        logging.debug("Clic action vider via texte impossible: %s", exc)

    clicked = await page.evaluate(
        """() => {
            const action = document.querySelector(
                '#rcm_folderlist .cmd_purge.active, #rcm_folderlist .cmd_purge, .cmd_purge.active, .cmd_purge'
            );
            if (!action) return false;
            action.click();
            return true;
        }"""
    )
    if clicked:
        logging.debug("Action vider cliquee via DOM click")
        return

    await save_debug_artifacts(page, "trash-purge-action-missing")
    raise RuntimeError("Action Roundcube 'vider la corbeille' introuvable")


def write_reports(
    items: list[ScanItem],
    quota: QuotaInfo,
    output_dir: Path,
    prefix: str,
    summaries: list[ScanSummary] | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = output_dir / f"{prefix}-{timestamp}.csv"
    json_path = output_dir / f"{prefix}-{timestamp}.json"

    fieldnames = list(asdict(items[0]).keys()) if items else list(ScanItem.__dataclass_fields__)
    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow(asdict(item))

    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "quota": asdict(quota),
        "count": len(items),
        "summaries": [asdict(summary) for summary in summaries or []],
        "items": [asdict(item) for item in items],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"csv": csv_path, "json": json_path}


def load_report_payload(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def parse_percent(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def parse_size(value: str) -> SizeInfo:
    raw = normalize_space(value)
    if not raw:
        return SizeInfo(raw=raw)

    match = re.search(r"([\d,.]+)\s*([KMGT]?o)", raw, re.IGNORECASE)
    if not match:
        return SizeInfo(raw=raw)

    amount = float(match.group(1).replace(",", "."))
    unit = match.group(2).lower()
    factors = {
        "o": 1,
        "ko": 1024,
        "mo": 1024**2,
        "go": 1024**3,
        "to": 1024**4,
    }
    byte_count = int(amount * factors.get(unit, 1))
    return SizeInfo(raw=raw, bytes=byte_count, mb=round(byte_count / 1024**2, 2))


def summarize_sizes(items: list[CleanupPlanItem]) -> dict:
    total_bytes = sum(parse_size(item.size).bytes for item in items)
    return {
        "bytes": total_bytes,
        "mb": round(total_bytes / 1024**2, 2),
        "gb": round(total_bytes / 1024**3, 3),
    }


def build_quota_projection(
    quota: dict,
    candidate_size: dict,
    trigger_over: int | None = None,
    target_under: int | None = None,
) -> dict:
    current_percent = parse_percent(quota.get("percent"))
    used = parse_size(str(quota.get("used", "")))
    total = parse_size(str(quota.get("total", "")))
    candidate_bytes = int(candidate_size.get("bytes") or 0)

    projection = {
        "current_percent": current_percent,
        "trigger_over": trigger_over,
        "trigger_met": trigger_over is None
        or (current_percent is not None and current_percent > trigger_over),
        "target_under": target_under,
        "candidate_bytes": candidate_bytes,
        "candidate_mb": round(candidate_bytes / 1024**2, 2),
    }

    if used.bytes <= 0 or total.bytes <= 0:
        return projection

    projected_used = max(used.bytes - candidate_bytes, 0)
    current_to_target = 0
    if target_under is not None:
        target_used = int(total.bytes * (target_under / 100))
        current_to_target = max(used.bytes - target_used, 0)

    projected_percent = round((projected_used / total.bytes) * 100, 1)
    projection.update(
        {
            "used_bytes": used.bytes,
            "total_bytes": total.bytes,
            "projected_used_bytes": projected_used,
            "projected_percent": projected_percent,
            "bytes_to_target": current_to_target,
            "mb_to_target": round(current_to_target / 1024**2, 2),
            "target_reached": target_under is None
            or projected_percent < target_under,
            "missing_bytes_to_target": max(current_to_target - candidate_bytes, 0),
            "missing_mb_to_target": round(
                max(current_to_target - candidate_bytes, 0) / 1024**2,
                2,
            ),
        }
    )
    return projection


def split_filter_values(values: list[str] | None) -> set[str]:
    result = set()
    for value in values or []:
        for part in value.split(","):
            part = part.strip()
            if part:
                result.add(part)
    return result


def load_cleanup_profiles(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Le fichier de profils doit contenir un objet JSON")
    return payload


def apply_cleanup_profile(args) -> dict:
    if not args.rules:
        return {}

    profiles = load_cleanup_profiles(Path(args.rules_file))
    if args.rules not in profiles:
        available = ", ".join(sorted(profiles))
        raise ValueError(f"Profil de nettoyage inconnu: {args.rules}. Disponibles: {available}")

    profile = profiles[args.rules]
    if not isinstance(profile, dict):
        raise ValueError(f"Profil de nettoyage invalide: {args.rules}")

    if args.quota_over is None and profile.get("quota_over") is not None:
        args.quota_over = int(profile["quota_over"])
    if args.quota_target_under is None and profile.get("quota_target_under") is not None:
        args.quota_target_under = int(profile["quota_target_under"])
    if not args.before and profile.get("before"):
        args.before = str(profile["before"])
    if not args.type and profile.get("types"):
        args.type = list(profile["types"])
    if not args.group and profile.get("groups"):
        args.group = list(profile["groups"])
    if not args.include_unread and profile.get("include_unread"):
        args.include_unread = True

    return profile


def item_matches_group(item: dict, allowed_groups: set[str]) -> bool:
    if not allowed_groups:
        return True
    matched = item.get("matched_keywords") or item.get("keyword") or ""
    groups = {part.strip() for part in matched.split("+") if part.strip()}
    return bool(groups & allowed_groups)


def build_cleanup_plan(args) -> int:
    setup_logging(verbose=args.verbose)
    profile = apply_cleanup_profile(args)
    report_path = Path(args.report)
    paths, selected_count = create_cleanup_plan(
        report_path=report_path,
        output_dir=Path(args.output_dir or FOLDERS["REPORTS"]),
        prefix=args.prefix or "cleanup-plan",
        rules_name=args.rules or "",
        profile=profile,
        quota_over=args.quota_over,
        quota_target_under=args.quota_target_under,
        before=args.before,
        types=args.type,
        groups=args.group,
        include_unread=args.include_unread,
    )

    logging.info("%s candidat(s) de nettoyage", selected_count)
    logging.info("CSV: %s", paths["csv"])
    logging.info("JSON: %s", paths["json"])
    return 0


def create_cleanup_plan(
    report_path: Path,
    output_dir: Path,
    prefix: str,
    rules_name: str = "",
    profile: dict | None = None,
    quota_over: int | None = None,
    quota_target_under: int | None = None,
    before: str | None = None,
    types: list[str] | None = None,
    groups: list[str] | None = None,
    include_unread: bool = False,
) -> tuple[dict, int]:
    payload = load_report_payload(report_path)
    items = payload.get("items", [])
    quota = payload.get("quota", {})
    quota_percent = parse_percent(quota.get("percent"))
    quota_required = quota_over is not None
    quota_ok = not quota_required or (
        quota_percent is not None and quota_percent > quota_over
    )

    allowed_types = split_filter_values(types)
    allowed_groups = split_filter_values(groups)
    before_date = dt.date.fromisoformat(before) if before else None

    selected_items: list[CleanupPlanItem] = []
    skipped = {
        "quota": 0,
        "date": 0,
        "type": 0,
        "group": 0,
        "unread": 0,
        "missing_date": 0,
    }

    for item in items:
        reasons = []

        if before_date:
            parsed_date = parse_mailiz_date(item.get("date", ""))
            if not parsed_date:
                skipped["missing_date"] += 1
                continue
            if parsed_date >= before_date:
                skipped["date"] += 1
                continue
            reasons.append(f"avant {before}")

        if allowed_types and item.get("document_type") not in allowed_types:
            skipped["type"] += 1
            continue
        if allowed_types:
            reasons.append("type " + item.get("document_type", ""))

        if not item_matches_group(item, allowed_groups):
            skipped["group"] += 1
            continue
        if allowed_groups:
            reasons.append("groupe " + (item.get("matched_keywords") or item.get("keyword") or ""))

        if item.get("unread") and not include_unread:
            skipped["unread"] += 1
            continue

        selected_items.append(
            CleanupPlanItem(
                selected=True,
                reason=", ".join(reasons) or "regles respectees",
                folder=item.get("folder", ""),
                uid=item.get("uid", ""),
                date=item.get("date", ""),
                size=item.get("size", ""),
                matched_keywords=item.get("matched_keywords", item.get("keyword", "")),
                document_type=item.get("document_type", ""),
                patient=item.get("patient", ""),
                sender=item.get("sender", ""),
                sender_email=item.get("sender_email", ""),
                subject=item.get("subject", ""),
                unread=bool(item.get("unread")),
                has_attachment=bool(item.get("has_attachment")),
            )
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = output_dir / f"{prefix}-{timestamp}.csv"
    json_path = output_dir / f"{prefix}-{timestamp}.json"

    fieldnames = list(CleanupPlanItem.__dataclass_fields__)
    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for item in selected_items:
            writer.writerow(asdict(item))

    candidate_size = summarize_sizes(selected_items)
    quota_projection = build_quota_projection(
        quota,
        candidate_size,
        trigger_over=quota_over,
        target_under=quota_target_under,
    )

    plan_payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_report": str(report_path),
        "quota": quota,
        "quota_condition_met": quota_ok,
        "rules": {
            "profile": rules_name,
            "profile_label": profile.get("label", "") if profile else "",
            "profile_description": profile.get("description", "") if profile else "",
            "quota_over": quota_over,
            "quota_target_under": quota_target_under,
            "before": before,
            "types": sorted(allowed_types),
            "groups": sorted(allowed_groups),
            "include_unread": include_unread,
        },
        "source_count": len(items),
        "candidate_count": len(selected_items),
        "candidate_size": candidate_size,
        "quota_projection": quota_projection,
        "skipped": skipped,
        "items": [asdict(item) for item in selected_items],
    }
    json_path.write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"csv": csv_path, "json": json_path}, len(selected_items)


async def scan_mailiz(args) -> int:
    validate_required_secrets()
    setup_logging(verbose=args.verbose)

    output_dir = Path(args.output_dir or FOLDERS["REPORTS"])
    all_items: list[ScanItem] = []
    summaries: list[ScanSummary] = []
    quota = QuotaInfo()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=args.headless)
        context = await browser.new_context(accept_downloads=False)
        page = await context.new_page()
        page.set_default_timeout(TIMEOUTS["element_wait"])

        try:
            await login_mailiz(page, delete_otp_email=args.delete_otp_email)
            quota = await get_mailbox_quota(page)
            logging.info("Quota Mailiz: %s", quota.raw or "non detecte")

            for folder in args.folder:
                logging.info("Ouverture du dossier %s", folder)
                await open_folder(page, folder)
                await show_unfiltered_folder(page)
                items, summary = await collect_paginated_results(
                    page,
                    folder,
                    OTHER_KEYWORD,
                    args.max_pages,
                    filter_keyword=False,
                )
                all_items.extend(items)
                summaries.append(summary)

        finally:
            await maybe_keep_browser_open(args)
            await context.close()
            await browser.close()

    all_items = apply_local_filters(deduplicate_items(all_items), args.before)
    all_items = filter_items_by_keywords(all_items, args.keyword)
    paths = write_reports(all_items, quota, output_dir, args.prefix, summaries)

    logging.info("%s message(s) candidat(s) exporte(s)", len(all_items))
    logging.info("CSV: %s", paths["csv"])
    logging.info("JSON: %s", paths["json"])
    return 0


def format_size_summary(size: dict) -> str:
    bytes_count = int(size.get("bytes") or 0)
    mb = float(size.get("mb") or 0)
    gb = float(size.get("gb") or 0)
    if gb >= 1:
        return f"{gb:.2f} Go"
    if bytes_count and mb < 0.1:
        return f"{bytes_count / 1024:.1f} ko"
    return f"{mb:.1f} Mo"


def print_trash_status(status: dict) -> None:
    count = status.get("reported_count")
    count_label = count if count is not None else status.get("scanned_count", 0)
    scope = "compteur Roundcube" if count is not None else "messages parcourus"
    print(
        f"Corbeille {status.get('folder')}: {count_label} message(s) ({scope}), "
        f"volume repere {format_size_summary(status.get('size') or {})}, "
        f"{status.get('pages_scanned', 0)} page(s) parcourue(s)."
    )


def print_quota(label: str, quota: QuotaInfo) -> None:
    if quota.raw:
        print(f"{label}: {quota.raw}")
        return
    if quota.percent is not None:
        print(f"{label}: {quota.percent}%")
        return
    print(f"{label}: quota non detecte")


def confirm_trash_emptying(confirm_value: str | None) -> bool:
    if confirm_value == TRASH_CONFIRMATION:
        return True
    typed = input(
        "Cette action vide definitivement la corbeille Mailiz. "
        f"Tapez exactement '{TRASH_CONFIRMATION}' pour confirmer: "
    )
    return typed == TRASH_CONFIRMATION


def confirm_move_to_trash(confirm_value: str | None) -> bool:
    if confirm_value == MOVE_CONFIRMATION:
        return True
    typed = input(
        "Cette action deplace des messages reels vers la corbeille Mailiz. "
        f"Tapez exactement '{MOVE_CONFIRMATION}' pour confirmer: "
    )
    return typed == MOVE_CONFIRMATION


def load_move_selection(path: Path) -> dict:
    payload = load_report_payload(path)
    if payload.get("action") != "move_to_trash":
        raise ValueError("le fichier selection n'est pas une selection de deplacement")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("selection vide")
    for item in items:
        if not item.get("folder") or not item.get("uid"):
            raise ValueError("selection invalide: chaque message doit avoir folder et uid")
    return payload


def summarize_selection(items: list[dict]) -> str:
    by_folder: dict[str, int] = {}
    for item in items:
        by_folder[item.get("folder", "")] = by_folder.get(item.get("folder", ""), 0) + 1
    parts = [f"{count} dans {folder}" for folder, count in sorted(by_folder.items())]
    return ", ".join(parts)


def append_move_log(entry: dict) -> None:
    log_path = FOLDERS["LOGS"] / "move-to-trash-actions.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def maybe_keep_browser_open(args) -> None:
    if not getattr(args, "keep_open", False):
        return
    if getattr(args, "headless", False):
        print("--keep-open ignore: Chromium est lance en mode headless.")
        return
    print("Chromium reste ouvert pour inspection. Appuyez sur Entree pour fermer et terminer.")
    await asyncio.to_thread(input)


async def trash_status_mailiz(args) -> int:
    validate_required_secrets()
    setup_logging(verbose=args.verbose or args.debug)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=args.headless)
        context = await browser.new_context(accept_downloads=False)
        page = await context.new_page()
        page.set_default_timeout(TIMEOUTS["element_wait"])

        try:
            await login_mailiz(page, delete_otp_email=args.delete_otp_email)
            status = await get_trash_status(page, args.folder, args.max_pages, debug=args.debug)
        finally:
            await maybe_keep_browser_open(args)
            await context.close()
            await browser.close()

    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print_trash_status(status)
    return 0


async def empty_trash_mailiz(args) -> int:
    setup_logging(verbose=args.verbose or args.debug)
    validate_required_secrets()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=args.headless)
        context = await browser.new_context(accept_downloads=False)
        page = await context.new_page()
        page.set_default_timeout(TIMEOUTS["element_wait"])

        try:
            await login_mailiz(page, delete_otp_email=args.delete_otp_email)
            quota_before = await get_mailbox_quota(page)
            print_quota("Quota avant vidage", quota_before)
            before = await get_trash_status(page, args.folder, args.max_pages, debug=args.debug)
            print_trash_status(before)
            if before.get("empty"):
                print("Corbeille deja vide. Aucune action lancee.")
                return 0

            if before.get("folder") != DEFAULT_TRASH_FOLDER:
                print(
                    f"Vidage refuse: dossier resolu {before.get('folder')!r}, "
                    f"attendu {DEFAULT_TRASH_FOLDER!r}."
                )
                return 2

            if not args.i_understand_this_deletes_trash:
                print(
                    "Vidage refuse: ajoutez le flag explicite "
                    "--i-understand-this-deletes-trash pour autoriser cette action."
                )
                return 2

            logging.info(
                "Action demandee: vider la corbeille %s contenant %s message(s)",
                before.get("folder"),
                before.get("reported_count") if before.get("reported_count") is not None else before.get("scanned_count"),
            )
            if not confirm_trash_emptying(args.confirm):
                print("Confirmation incorrecte. Corbeille conservee.")
                logging.info("Confirmation utilisateur refusee ou incorrecte")
                return 2

            logging.info("Confirmation utilisateur validee")
            await purge_trash(page, before.get("folder") or args.folder)
            after = await get_trash_status(page, before.get("folder") or args.folder, args.max_pages, debug=args.debug)
            quota_after = await get_mailbox_quota(page)
        finally:
            await maybe_keep_browser_open(args)
            await context.close()
            await browser.close()

    print("Corbeille videe via Roundcube.")
    print_trash_status(after)
    print_quota("Quota apres vidage", quota_after)
    return 0


async def find_message_row_by_uid(page, uid: str):
    selector = f'#messagelist tbody tr:has(a[href*="_uid={uid}"]), tr.message:has(a[href*="_uid={uid}"])'
    row = page.locator(selector).first
    if await row.count() and await row.is_visible():
        return row
    return None


async def select_message_uid(page, uid: str) -> bool:
    row = await find_message_row_by_uid(page, uid)
    if row is None:
        return False
    try:
        checkbox = row.locator("input[type='checkbox']").first
        if await checkbox.count():
            await checkbox.check(force=True)
            return True
    except Exception as exc:
        logging.debug("Selection checkbox impossible pour uid=%s: %s", uid, exc)

    try:
        await row.click()
        return True
    except Exception as exc:
        logging.debug("Selection ligne impossible pour uid=%s: %s", uid, exc)
        return False


async def move_uid_to_trash(page, folder: str, uid: str) -> bool:
    return await move_uids_to_trash(page, folder, [uid])


async def move_uids_to_trash(page, folder: str, uids: list[str]) -> bool:
    clean_uids = [str(uid).strip() for uid in uids if str(uid).strip()]
    if not clean_uids:
        return False
    result = await page.evaluate(
        """async ({ folder, uids, target }) => {
            const token = window.rcmail?.env?.request_token || '';
            const params = new URLSearchParams();
            params.set('_uid', uids.join(','));
            params.set('_mbox', folder);
            params.set('_target_mbox', target);
            params.set('_remote', '1');
            if (token) params.set('_token', token);

            const response = await fetch('/?_task=mail&_action=move', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: params.toString(),
                credentials: 'same-origin',
            });
            const text = await response.text();
            return { ok: response.ok, status: response.status, text: text.slice(0, 500) };
        }""",
        {"folder": folder, "uids": clean_uids, "target": DEFAULT_TRASH_FOLDER},
    )
    logging.debug(
        "POST move batch count=%s folder=%s status=%s ok=%s body=%s",
        len(clean_uids),
        folder,
        result.get("status"),
        result.get("ok"),
        result.get("text"),
    )
    if result.get("ok"):
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(700)
        return True
    return False


def batched(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def move_selected_messages_to_trash(page, uid: str) -> None:
    moved = await page.evaluate(
        """(messageUid) => {
            if (window.rcmail && window.rcmail.message_list && typeof window.rcmail.message_list.select === 'function') {
                window.rcmail.message_list.select(messageUid);
            }
            if (window.rcmail && typeof window.rcmail.command === 'function') {
                window.rcmail.command('delete');
                return true;
            }
            return false;
        }""",
        uid,
    )
    if moved:
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1200)
        return

    moved = await page.evaluate(
        """() => {
            if (window.rcmail && typeof window.rcmail.command === 'function') {
                window.rcmail.command('moveto', 'Trash');
                return true;
            }
            return false;
        }"""
    )
    if moved:
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1200)
        return

    for selector in (".button.move", ".cmd_moveto", ".toolbar .move"):
        button = page.locator(selector).first
        try:
            await button.wait_for(state="visible", timeout=1500)
            await button.click()
            await page.locator('[role="menuitem"]:has-text("Corbeille"), a:has-text("Corbeille")').first.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(1200)
            return
        except Exception as exc:
            logging.debug("Deplacement via %s impossible: %s", selector, exc)

    await save_debug_artifacts(page, "move-to-trash-action-missing")
    raise RuntimeError("Action Roundcube de deplacement vers corbeille introuvable")


async def move_selection_to_trash_mailiz(args) -> int:
    setup_logging(verbose=args.verbose or args.debug)
    selection_path = Path(args.selection)
    selection = load_move_selection(selection_path)
    items = selection.get("items", [])
    unread_count = sum(1 for item in items if item.get("unread"))
    if unread_count and not args.include_unread:
        print(f"Deplacement refuse: {unread_count} message(s) non lu(s) dans la selection.")
        return 2
    trash_count = sum(1 for item in items if item.get("folder") == DEFAULT_TRASH_FOLDER)
    if trash_count:
        print(f"Deplacement refuse: {trash_count} message(s) deja dans la corbeille.")
        return 2

    print(f"Selection: {len(items)} message(s) a deplacer vers Trash ({summarize_selection(items)}).")
    if args.dry_run:
        print("Mode dry-run: aucune action Mailiz.")
        return 0
    if not args.i_understand_this_moves_mail:
        print(
            "Deplacement refuse: ajoutez le flag explicite "
            "--i-understand-this-moves-mail pour autoriser cette action."
        )
        return 2
    if not confirm_move_to_trash(args.confirm):
        print("Confirmation incorrecte. Aucun message deplace.")
        return 2
    if args.empty_trash_after:
        if not args.i_understand_this_deletes_trash:
            print(
                "Vidage refuse: ajoutez le flag explicite "
                "--i-understand-this-deletes-trash pour autoriser le vidage de la corbeille."
            )
            return 2
        if not confirm_trash_emptying(args.empty_trash_confirm):
            print("Confirmation vidage incorrecte. Aucun message deplace.")
            return 2

    validate_required_secrets()
    moved_items = []
    missing_items = []
    failed_items = []
    final_trash_status = None
    trash_after_empty = None
    quota_before_empty = None
    quota_after_empty = None

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=args.headless)
        context = await browser.new_context(accept_downloads=False)
        page = await context.new_page()
        page.set_default_timeout(TIMEOUTS["element_wait"])

        try:
            await login_mailiz(page, delete_otp_email=args.delete_otp_email)
            by_folder: dict[str, list[dict]] = {}
            for item in items:
                by_folder.setdefault(item.get("folder", ""), []).append(item)

            for folder, folder_items in by_folder.items():
                logging.info("Ouverture du dossier %s pour %s message(s)", folder, len(folder_items))
                await open_folder(page, folder)
                await show_unfiltered_folder(page)
                item_batches = ([item] for item in folder_items) if args.capture_each else batched(folder_items, MOVE_BATCH_SIZE)
                for batch in item_batches:
                    batch = list(batch)
                    batch_uids = [str(item.get("uid", "")).strip() for item in batch if str(item.get("uid", "")).strip()]
                    try:
                        if (
                            not args.capture_each
                            and len(batch_uids) == len(batch)
                            and await move_uids_to_trash(page, folder, batch_uids)
                        ):
                            moved_items.extend(batch)
                            continue

                        if len(batch) > 1:
                            logging.warning(
                                "Lot refuse dans %s (%s message(s)), reprise message par message",
                                folder,
                                len(batch),
                            )
                        for item in batch:
                            uid = str(item.get("uid", "")).strip()
                            if args.capture_each:
                                await save_inspection_artifacts(page, f"move-to-trash-before-{slugify(folder)}-{uid}")
                            if uid and await move_uid_to_trash(page, folder, uid):
                                moved_items.append(item)
                                continue
                            if not await select_message_uid(page, uid):
                                missing_items.append(item)
                                logging.warning("UID introuvable dans %s: %s", folder, uid)
                                continue
                            await move_selected_messages_to_trash(page, uid)
                            moved_items.append(item)
                            await open_folder(page, folder)
                            await show_unfiltered_folder(page)
                    except Exception:
                        failed_items.extend(batch)
                        raise

            if args.final_trash_status or args.empty_trash_after:
                final_trash_status = await get_trash_status(
                    page,
                    DEFAULT_TRASH_FOLDER,
                    args.final_trash_max_pages,
                    debug=args.debug,
                )
            if args.empty_trash_after:
                quota_before_empty = await get_mailbox_quota(page)
                if final_trash_status and final_trash_status.get("empty"):
                    logging.info("Corbeille deja vide apres deplacement")
                else:
                    logging.info("Vidage de la corbeille dans la meme session")
                    await purge_trash(page, DEFAULT_TRASH_FOLDER)
                trash_after_empty = await get_trash_status(
                    page,
                    DEFAULT_TRASH_FOLDER,
                    args.final_trash_max_pages,
                    debug=args.debug,
                )
                quota_after_empty = await get_mailbox_quota(page)
        finally:
            await maybe_keep_browser_open(args)
            await context.close()
            await browser.close()

    log_entry = {
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "action": "move_to_trash",
        "selection": str(selection_path),
        "source_plan": selection.get("source_plan", ""),
        "requested_count": len(items),
        "moved_count": len(moved_items),
        "missing_count": len(missing_items),
        "failed_count": len(failed_items),
        "final_trash_status": final_trash_status,
        "empty_trash_after": bool(args.empty_trash_after),
        "trash_after_empty": trash_after_empty,
        "quota_before_empty": asdict(quota_before_empty) if quota_before_empty else None,
        "quota_after_empty": asdict(quota_after_empty) if quota_after_empty else None,
        "moved": [{"folder": item.get("folder"), "uid": item.get("uid"), "subject": item.get("subject", "")} for item in moved_items],
        "missing": [{"folder": item.get("folder"), "uid": item.get("uid"), "subject": item.get("subject", "")} for item in missing_items],
    }
    append_move_log(log_entry)
    print(f"Deplacement termine: {len(moved_items)} deplace(s), {len(missing_items)} introuvable(s).")
    if final_trash_status:
        print_trash_status(final_trash_status)
    if args.empty_trash_after:
        print("Corbeille videe dans la meme session.")
        if trash_after_empty:
            print_trash_status(trash_after_empty)
        if quota_before_empty:
            print_quota("Quota avant vidage", quota_before_empty)
        if quota_after_empty:
            print_quota("Quota apres vidage", quota_after_empty)
    print(f"Journal: {FOLDERS['LOGS'] / 'move-to-trash-actions.jsonl'}")
    return 0 if not missing_items and not failed_items else 1


def add_browser_mode_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=False,
        help="Lancer Chromium sans fenetre. Non recommande car Mailiz peut bloquer ce mode.",
    )
    command.add_argument(
        "--headed",
        dest="headless",
        action="store_false",
        help="Afficher la fenetre Chromium pendant l'action. Active par defaut.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MailizClean - scan Mailiz non destructif avec export CSV/JSON"
    )
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="Scanner Mailiz sans suppression")
    add_browser_mode_arguments(scan)
    scan.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Filtre local optionnel apres scan. Peut etre repete. Le scan ne relance plus de recherche Roundcube.",
    )
    scan.add_argument(
        "--folder",
        action="append",
        default=[],
        help='Dossier Roundcube a scanner, ex: INBOX, Sent, Trash. Defaut: INBOX',
    )
    scan.add_argument(
        "--before",
        help="Filtrer localement les resultats strictement avant cette date ISO, ex: 2024-01-01",
    )
    scan.add_argument(
        "--output-dir",
        help="Dossier de sortie des rapports. Defaut: data/reports",
    )
    scan.add_argument("--prefix", default="mailiz-scan", help="Prefixe des rapports")
    scan.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Nombre maximal de pages a parcourir par passe de scan. 0 = toutes les pages.",
    )
    scan.add_argument(
        "--skip-other",
        action="store_true",
        help="Option conservee pour compatibilite, sans effet: le scan est toujours mono-passe.",
    )
    scan.add_argument(
        "--delete-otp-email",
        action="store_true",
        help="Supprimer le mail OTP apres lecture. Desactive par defaut.",
    )
    scan.add_argument(
        "--keep-open",
        action="store_true",
        help="Garder Chromium ouvert en fin de commande jusqu'a appui sur Entree.",
    )
    scan.add_argument("--verbose", action="store_true", help="Logs plus detailles")

    dashboard = subparsers.add_parser("dashboard", help="Lancer le dashboard local de revue")
    dashboard.add_argument("--host", default="127.0.0.1", help="Adresse d'ecoute locale")
    dashboard.add_argument("--port", type=int, default=8765, help="Port du dashboard")
    dashboard.add_argument(
        "--report-dir",
        default=str(FOLDERS["REPORTS"]),
        help="Dossier contenant les rapports JSON",
    )

    app = subparsers.add_parser(
        "app",
        help="Lancer MailizClean comme utilitaire local avec ouverture du navigateur",
    )
    app.add_argument("--host", default="127.0.0.1", help="Adresse locale")
    app.add_argument("--port", type=int, default=8765, help="Port local prefere")
    app.add_argument(
        "--no-browser",
        action="store_true",
        help="Ne pas ouvrir automatiquement le navigateur",
    )

    trash_status = subparsers.add_parser(
        "trash-status",
        help="Examiner la corbeille Mailiz sans modification",
    )
    add_browser_mode_arguments(trash_status)
    trash_status.add_argument(
        "--folder",
        default=DEFAULT_TRASH_FOLDER,
        help="Dossier Roundcube de corbeille. Defaut: Trash",
    )
    trash_status.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Nombre maximal de pages a parcourir. 0 = toutes les pages.",
    )
    trash_status.add_argument(
        "--json",
        action="store_true",
        help="Afficher le statut brut en JSON",
    )
    trash_status.add_argument(
        "--delete-otp-email",
        action="store_true",
        help="Supprimer le mail OTP apres lecture. Desactive par defaut.",
    )
    trash_status.add_argument(
        "--debug",
        action="store_true",
        help="Logs DEBUG et liste des dossiers Roundcube detectes.",
    )
    trash_status.add_argument(
        "--keep-open",
        action="store_true",
        help="Garder Chromium ouvert en fin de commande jusqu'a appui sur Entree.",
    )
    trash_status.add_argument("--verbose", action="store_true", help="Logs plus detailles")

    empty_trash = subparsers.add_parser(
        "empty-trash",
        help="Vider la corbeille Mailiz apres confirmation explicite",
    )
    add_browser_mode_arguments(empty_trash)
    empty_trash.add_argument(
        "--folder",
        default=DEFAULT_TRASH_FOLDER,
        help="Dossier Roundcube de corbeille. Defaut: Trash",
    )
    empty_trash.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Nombre maximal de pages a parcourir pour l'estimation avant/apres. 0 = toutes les pages.",
    )
    empty_trash.add_argument(
        "--confirm",
        help=f"Phrase obligatoire pour automatiser la confirmation: {TRASH_CONFIRMATION}",
    )
    empty_trash.add_argument(
        "--i-understand-this-deletes-trash",
        action="store_true",
        help="Flag explicite requis pour autoriser le vidage reel de la corbeille.",
    )
    empty_trash.add_argument(
        "--delete-otp-email",
        action="store_true",
        help="Supprimer le mail OTP apres lecture. Desactive par defaut.",
    )
    empty_trash.add_argument(
        "--debug",
        action="store_true",
        help="Logs DEBUG et liste des dossiers Roundcube detectes.",
    )
    empty_trash.add_argument(
        "--keep-open",
        action="store_true",
        help="Garder Chromium ouvert en fin de commande jusqu'a appui sur Entree.",
    )
    empty_trash.add_argument("--verbose", action="store_true", help="Logs plus detailles")

    move = subparsers.add_parser(
        "move-to-trash",
        help="Deplacer vers la corbeille les messages d'une selection exportee",
    )
    move.add_argument("--selection", required=True, help="Fichier JSON move-selection exporte par le dashboard")
    add_browser_mode_arguments(move)
    move.add_argument(
        "--dry-run",
        action="store_true",
        help="Lire et resumer la selection sans se connecter a Mailiz.",
    )
    move.add_argument(
        "--i-understand-this-moves-mail",
        action="store_true",
        help="Flag explicite requis pour autoriser le deplacement reel vers la corbeille.",
    )
    move.add_argument(
        "--confirm",
        help=f"Phrase obligatoire pour automatiser la confirmation: {MOVE_CONFIRMATION}",
    )
    move.add_argument(
        "--include-unread",
        action="store_true",
        help="Autoriser les messages non lus presents dans la selection. Refuse par defaut.",
    )
    move.add_argument(
        "--delete-otp-email",
        action="store_true",
        help="Supprimer le mail OTP apres lecture. Desactive par defaut.",
    )
    move.add_argument(
        "--debug",
        action="store_true",
        help="Logs DEBUG.",
    )
    move.add_argument(
        "--capture-each",
        action="store_true",
        help="Ecrire une capture HTML/PNG avant chaque message deplace.",
    )
    move.add_argument(
        "--final-trash-status",
        action="store_true",
        help="Afficher l'etat de la corbeille dans la meme connexion apres deplacement.",
    )
    move.add_argument(
        "--final-trash-max-pages",
        type=int,
        default=1,
        help="Pages a parcourir pour le statut corbeille final. Defaut: 1.",
    )
    move.add_argument(
        "--empty-trash-after",
        action="store_true",
        help="Vider la corbeille dans la meme connexion apres deplacement.",
    )
    move.add_argument(
        "--i-understand-this-deletes-trash",
        action="store_true",
        help="Flag explicite requis avec --empty-trash-after.",
    )
    move.add_argument(
        "--empty-trash-confirm",
        help=f"Phrase obligatoire avec --empty-trash-after: {TRASH_CONFIRMATION}",
    )
    move.add_argument(
        "--keep-open",
        action="store_true",
        help="Garder Chromium ouvert en fin de commande jusqu'a appui sur Entree.",
    )
    move.add_argument("--verbose", action="store_true", help="Logs plus detailles")

    plan = subparsers.add_parser(
        "plan-cleanup",
        help="Preparer un plan de nettoyage depuis un rapport, sans action Mailiz",
    )
    plan.add_argument("--report", required=True, help="Rapport JSON source")
    plan.add_argument(
        "--rules",
        help="Nom d'un profil dans config/cleanup_rules.json",
    )
    plan.add_argument(
        "--rules-file",
        default=str(DEFAULT_RULES_PATH),
        help="Fichier JSON de profils de nettoyage",
    )
    plan.add_argument(
        "--quota-over",
        type=int,
        help="Seuil indicatif de declenchement du scenario, sans masquer les candidats",
    )
    plan.add_argument(
        "--quota-target-under",
        type=int,
        help="Objectif indicatif de quota apres nettoyage, sans limiter automatiquement la liste",
    )
    plan.add_argument("--before", help="Inclure seulement les messages avant cette date ISO")
    plan.add_argument(
        "--type",
        action="append",
        default=[],
        help="Type documentaire autorise. Peut etre repete ou separe par virgules.",
    )
    plan.add_argument(
        "--group",
        action="append",
        default=[],
        help="Groupe autorise: hnet, XDM, hnet+XDM, autres. Peut etre repete.",
    )
    plan.add_argument(
        "--include-unread",
        action="store_true",
        help="Inclure les messages non lus. Par defaut ils sont exclus.",
    )
    plan.add_argument("--output-dir", help="Dossier de sortie du plan. Defaut: data/reports")
    plan.add_argument("--prefix", default="cleanup-plan", help="Prefixe des fichiers de plan")
    plan.add_argument("--verbose", action="store_true", help="Logs plus detailles")

    return parser


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "scan":
        args.folder = args.folder or list(DEFAULT_FOLDERS)
        return await scan_mailiz(args)

    if args.command == "dashboard":
        from mailiz_dashboard import run_dashboard

        return run_dashboard(args.host, args.port, Path(args.report_dir))

    if args.command == "app":
        from mailiz_app import run_app

        return run_app(args.host, args.port, open_browser=not args.no_browser)

    if args.command == "trash-status":
        return await trash_status_mailiz(args)

    if args.command == "empty-trash":
        return await empty_trash_mailiz(args)

    if args.command == "move-to-trash":
        return await move_selection_to_trash_mailiz(args)

    if args.command == "plan-cleanup":
        return build_cleanup_plan(args)

    parser.error(f"Commande inconnue: {args.command}")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Arret demande par l'utilisateur", file=sys.stderr)
        sys.exit(130)
