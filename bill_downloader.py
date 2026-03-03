"""
bill_downloader.py
==================
Download automático de fatura Enel após autenticação.

Fluxo
-----
  1. Navega para a área "Minhas Faturas" do portal
     (tenta URL direta; se redirecionar, usa links do menu autenticado)
  2. Aguarda conteúdo dinâmico carregar (portal usa JS pesado)
  3. Localiza o link/botão de download da fatura mais recente
     (filtra links de privacidade/termos)
  4. Scrolla o elemento até a viewport e clica
  5. Fallback: detecta URL do PDF e baixa via requests (reutiliza cookies)

Configuração
------------
  OUTPUT_DIR → pasta onde os PDFs serão salvos (criada automaticamente)
"""

import time
import logging
from pathlib import Path
from datetime import datetime

import requests
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger(__name__)

# ── Configuração ──────────────────────────────────────────────────────────────
OUTPUT_DIR = Path("faturas")

# URLs a tentar, em ordem de preferência
_BILLS_URLS = [
    "https://www.enel.com.br/pt-ceara/private-area.html",
    "https://www.enel.com.br/pt-ceara/area-cliente/minhas-faturas.html",
    "https://www.enel.com.br/pt-ceara/area-cliente/segunda-via-de-conta.html",
    "https://www.enel.com.br/pt-ceara/area-cliente.html",
]

# Textos/hrefs de links a IGNORAR (privacidade, termos, etc.)
_EXCLUDE_KEYWORDS = [
    "privacidade", "privacy", "aviso de privacidade", "politica",
    "política", "termos", "terms", "cookies", "lgpd", "elena",
]

# Links de menu (página inicial autenticada) que levam à área de faturas
_MENU_BILL_SELECTORS = [
    (By.XPATH, "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
               "'abcdefghijklmnopqrstuvwxyz'),'minhas faturas')]"),
    (By.XPATH, "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
               "'abcdefghijklmnopqrstuvwxyz'),'segunda via')]"),
    (By.CSS_SELECTOR, "a[href*='minhas-faturas']"),
    (By.CSS_SELECTOR, "a[href*='segunda-via']"),
    (By.XPATH, "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
               "'abcdefghijklmnopqrstuvwxyz'),'fatura')]"),
    (By.CSS_SELECTOR, "a[href*='fatura']"),
]

# Seletores do botão/link de download da fatura — do mais específico ao mais genérico
_BILL_LINK_SELECTORS = [
    # ── Enel Ceará — seletores confirmados ───────────────────────────────────
    (By.CSS_SELECTOR, "button.download-pdf.btn-account-invoices"),
    (By.CSS_SELECTOR, "button.download-pdf"),
    (By.CSS_SELECTOR, "button.btn-account-invoices"),
    (By.XPATH, "//button[contains(normalize-space(),'BAIXAR CONTA')]"),
    (By.XPATH, "//button[contains(normalize-space(),'Baixar Conta')]"),
    (By.XPATH, "//button[@data-invoice]"),
    # ── Genéricos de portais de energia ──────────────────────────────────────
    (By.CSS_SELECTOR, "a.download-invoice, a.btn-download, button.download-invoice"),
    (By.CSS_SELECTOR, "a.invoice-download, .fatura-download a, .bill-download a"),
    (By.CSS_SELECTOR, "[data-action*='download'], [data-component*='download']"),
    (By.CSS_SELECTOR, "a[href*='fatura'][href*='pdf']"),
    (By.CSS_SELECTOR, "a[href*='boleto'][href*='pdf']"),
    (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
               "'abcdefghijklmnopqrstuvwxyz'),'baixar fatura')]"),
    (By.XPATH, "//a[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
               "'abcdefghijklmnopqrstuvwxyz'),'baixar fatura')]"),
    (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
               "'abcdefghijklmnopqrstuvwxyz'),'baixar')]"),
    (By.XPATH, "//a[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
               "'abcdefghijklmnopqrstuvwxyz'),'baixar')]"),
    # Genérico — qualquer PDF (last resort, já filtrado por _EXCLUDE_KEYWORDS)
    (By.CSS_SELECTOR, "a[href*='.pdf']"),
    (By.CSS_SELECTOR, "a[href$='.pdf']"),
]


# ══════════════════════════════════════════════════════════════════════════════
# UTILITÁRIOS
# ══════════════════════════════════════════════════════════════════════════════

def dismiss_lgpd_popup(driver, timeout: int = 5):
    """
    Fecha o popup de confirmação de dados LGPD clicando em 'SIM'.
    Aparece após o login no portal Enel Ceará.
    timeout: segundos para aguardar cada seletor (padrão 5).
    """
    _LGPD_SELECTORS = [
        (By.ID,           "popupLgpdButtonDataConfirmationYes"),
        (By.CSS_SELECTOR, "button.btnPopupDataConfirmationLgpd"),
        (By.CSS_SELECTOR, "button.latam-btn-cta--pink"),
        (By.XPATH,        "//button[normalize-space()='SIM' and contains(@class,'PopupDataConfirmation')]"),
    ]
    for by, sel in _LGPD_SELECTORS:
        try:
            btn = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, sel)))
            log.info(f"[LGPD] Popup LGPD detectado — clicando em SIM ({sel}).")
            _scroll_into_view(driver, btn)
            btn.click()
            time.sleep(1.5)
            return
        except TimeoutException:
            continue
    log.debug("[LGPD] Popup LGPD não encontrado (normal se já foi aceito antes).")


def _ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    return OUTPUT_DIR


def _scroll_into_view(driver, elem):
    """Scrolla o elemento até o centro da viewport."""
    driver.execute_script(
        "arguments[0].scrollIntoView({behavior:'instant', block:'center'});", elem
    )
    time.sleep(0.4)


def _is_excluded(text: str, href: str) -> bool:
    """Retorna True se o link parece ser de privacidade/termos (não é fatura)."""
    combined = (text + " " + href).lower()
    return any(kw in combined for kw in _EXCLUDE_KEYWORDS)


def _wait_for_new_file(directory: Path, timeout: int = 45) -> "Path | None":
    """
    Aguarda um novo arquivo (não-.crdownload) aparecer no diretório.
    Retorna o Path do arquivo ou None se timeout.
    """
    before = set(directory.glob("*"))
    deadline = time.time() + timeout
    log.info(f"[Download] Aguardando arquivo em '{directory}'...")
    while time.time() < deadline:
        after = set(directory.glob("*"))
        new = after - before
        if new:
            candidate = sorted(new, key=lambda p: p.stat().st_mtime)[-1]
            if not str(candidate).endswith(".crdownload"):
                log.info(f"[Download] Arquivo recebido: {candidate.name}")
                return candidate
        time.sleep(0.8)
    log.warning("[Download] Timeout aguardando arquivo.")
    return None


def _save_pdf(driver, url: str, directory: Path) -> "Path | None":
    """
    Baixa um PDF diretamente via requests,
    reutilizando os cookies ativos da sessão do Chrome.
    """
    try:
        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        ua = driver.execute_script("return navigator.userAgent")
        log.info(f"[Download] Baixando PDF direto: {url[:80]}...")
        resp = requests.get(
            url,
            cookies=cookies,
            headers={"User-Agent": ua},
            timeout=30,
            allow_redirects=True,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type and len(resp.content) < 1000:
            log.warning(f"[Download] Conteúdo suspeito (Content-Type: {content_type}).")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = directory / f"fatura_enel_{timestamp}.pdf"
        with open(filename, "wb") as f:
            f.write(resp.content)
        log.info(f"[Download] Salvo: {filename} ({len(resp.content) / 1024:.1f} KB)")
        return filename

    except Exception as exc:
        log.error(f"[Download] Erro ao baixar PDF: {exc}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# NAVEGAÇÃO
# ══════════════════════════════════════════════════════════════════════════════

def _click_menu_bills_link(driver) -> bool:
    """
    Tenta encontrar e clicar em links de faturas na página atual (menu/homepage
    autenticada). Aguarda redirecionamento após clique.
    """
    for by, sel in _MENU_BILL_SELECTORS:
        try:
            elems = driver.find_elements(by, sel)
            for elem in elems:
                if not elem.is_displayed():
                    continue
                text = (elem.text or elem.get_attribute("textContent") or "").strip()
                href = elem.get_attribute("href") or ""
                if _is_excluded(text, href):
                    continue
                log.info(f"[Faturas] Link de menu encontrado: '{text or href[:60]}'")
                _scroll_into_view(driver, elem)
                try:
                    elem.click()
                except ElementClickInterceptedException:
                    driver.execute_script("arguments[0].click();", elem)
                time.sleep(4)
                current = driver.current_url
                log.info(f"[Faturas] Após clique no menu: {current}")
                return True
        except Exception as exc:
            log.debug(f"[Faturas] Menu seletor {sel!r} falhou: {exc}")
            continue
    return False


def _wait_for_bill_button(driver, timeout: int = 60) -> bool:
    """
    Aguarda até `timeout` segundos pelo botão 'BAIXAR CONTA' (button.download-pdf).
    Retorna True se encontrado.
    """
    log.info(f"[Faturas] Aguardando botão de download (até {timeout}s)...")
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "button.download-pdf, button.btn-account-invoices")
            )
        )
        log.info("[Faturas] Botão de download detectado na página.")
        return True
    except TimeoutException:
        log.warning("[Faturas] Botão de download não apareceu dentro do timeout.")
        return False


def navigate_to_bills(driver) -> bool:
    """
    Navega para a área de faturas com múltiplos fallbacks.
    Retorna False somente se sessão expirou (redirecionado para login).
    """
    login_redirects = 0  # conta quantas URLs deram redirect para login
    for url in _BILLS_URLS:
        log.info(f"[Faturas] Tentando URL: {url}")
        driver.get(url)
        time.sleep(5)  # aguarda JS pesado do portal carregar

        current = driver.current_url
        log.info(f"[Faturas] URL atual: {current}")

        if "login" in current.lower():
            login_redirects += 1
            log.warning(f"[Faturas] URL {url} redirecionou para login ({login_redirects}ª vez).")
            # Só aborta se TODAS as URLs redirecionarem para login
            if login_redirects >= len(_BILLS_URLS):
                log.error("[Faturas] Todas as URLs redirecionaram para login — sessão expirou.")
                return False
            log.info("[Faturas] Tentando próxima URL...")
            continue

        # Checa popup LGPD (pode aparecer ao acessar área autenticada)
        dismiss_lgpd_popup(driver, timeout=5)

        # Se chegou na área correta, aguarda conteúdo dinâmico e retorna
        if any(kw in current.lower() for kw in ["fatura", "segunda-via", "area-cliente", "private-area"]):
            log.info("[Faturas] URL da área do cliente detectada.")
            _wait_for_bill_button(driver, timeout=60)
            return True

        # Redirecionado para homepage — tenta navegar via menu
        log.info("[Faturas] Redirecionado — buscando link de faturas no menu...")
        if _click_menu_bills_link(driver):
            current2 = driver.current_url
            if "login" in current2.lower():
                login_redirects += 1
                log.warning("[Faturas] Menu também redirecionou para login.")
                continue
            log.info(f"[Faturas] Após clique no menu: {current2}")
            # Aguarda popup LGPD e o botão de download aparecerem (conteúdo AJAX)
            dismiss_lgpd_popup(driver, timeout=8)
            _wait_for_bill_button(driver, timeout=60)
            return True

        log.warning(f"[Faturas] Não encontrou link de faturas no menu em: {current}")

    # Mesmo sem encontrar a URL ideal, continua e tenta os seletores
    log.warning("[Faturas] Nenhuma URL de faturas confirmada — tentando seletores mesmo assim.")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# LOCALIZAÇÃO E DOWNLOAD DA FATURA
# ══════════════════════════════════════════════════════════════════════════════

def _try_click_elem(driver, elem) -> bool:
    """Tenta clicar no elemento (Selenium → JS fallback). Retorna True se OK."""
    _scroll_into_view(driver, elem)
    try:
        elem.click()
        return True
    except ElementClickInterceptedException:
        log.debug("[Faturas] Clique interceptado — tentando via JS.")
        try:
            driver.execute_script("arguments[0].click();", elem)
            return True
        except Exception as exc:
            log.debug(f"[Faturas] JS click falhou: {exc}")
            return False
    except Exception as exc:
        log.debug(f"[Faturas] Clique falhou: {exc}")
        return False


def _find_and_click_bill(driver, directory: Path) -> "Path | None":
    """
    Percorre os seletores, itera TODOS os elementos encontrados (filtrando os
    que parecem privacidade/termos), scrolla até o candidato e tenta clicar.
    Lida com abertura em nova aba (PDF viewer) baixando via requests.
    """
    original_handles = set(driver.window_handles)

    for by, sel in _BILL_LINK_SELECTORS:
        # Coleta todos os elementos sem wait agressivo (5s)
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((by, sel))
            )
        except TimeoutException:
            continue

        elems = driver.find_elements(by, sel)
        if not elems:
            continue

        log.info(f"[Faturas] Seletor '{sel}' → {len(elems)} elemento(s)")

        for elem in elems:
            try:
                href = elem.get_attribute("href") or ""
                text = (elem.text or elem.get_attribute("textContent") or "").strip()

                if _is_excluded(text, href):
                    log.info(f"[Faturas] Ignorando (privacidade/termos): '{text or href[:50]}'")
                    continue

                if not elem.is_displayed():
                    log.debug(f"[Faturas] Elemento não visível: '{text or href[:50]}'")
                    continue

                log.info(f"[Faturas] Candidato: '{text or href[:70]}'")

                if not _try_click_elem(driver, elem):
                    log.warning("[Faturas] Não foi possível clicar — próximo elemento.")
                    continue

                time.sleep(2)

                # Caso abra em nova aba (PDF viewer do Chrome)
                new_handles = set(driver.window_handles) - original_handles
                if new_handles:
                    driver.switch_to.window(new_handles.pop())
                    pdf_url = driver.current_url
                    log.info(f"[Faturas] PDF em nova aba: {pdf_url[:80]}")
                    driver.close()
                    driver.switch_to.window(list(original_handles)[0])
                    return _save_pdf(driver, pdf_url, directory)

                # Caso inicie download direto do Chrome
                result = _wait_for_new_file(directory, timeout=30)
                if result:
                    return result

                # Caso href aponte direto para PDF
                if href and ("pdf" in href.lower() or href.lower().endswith(".pdf")):
                    return _save_pdf(driver, href, directory)

                # Sem resultado — tenta próximo elemento
                log.warning("[Faturas] Clique não gerou download nem nova aba; próximo candidato.")

            except Exception as exc:
                log.debug(f"[Faturas] Elemento falhou: {exc}")
                continue

    # Diagnóstico: lista todos os PDFs encontrados na página para debug
    all_pdfs = driver.find_elements(By.CSS_SELECTOR, "a[href*='.pdf'], a[href*='pdf']")
    log.warning(f"[Faturas] Diagnóstico — {len(all_pdfs)} link(s) com 'pdf' na página:")
    for el in all_pdfs[:10]:
        log.warning(f"  href={el.get_attribute('href') or '?':80s}  text='{el.text.strip()}'")

    log.error("[Faturas] Nenhum link de fatura encontrado com os seletores disponíveis.")
    log.info("[Faturas] Dica: inspecione o portal e adicione o seletor correto "
             "em _BILL_LINK_SELECTORS em bill_downloader.py.")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# PONTO DE ENTRADA PÚBLICO
# ══════════════════════════════════════════════════════════════════════════════

def download_bill(driver) -> "Path | None":
    """
    Navega para a área de faturas e baixa a fatura mais recente.
    Retorna o Path do arquivo PDF salvo, ou None em caso de falha.
    """
    directory = _ensure_output_dir()

    if not navigate_to_bills(driver):
        return None

    return _find_and_click_bill(driver, directory)
