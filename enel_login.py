"""
Enel Login Automation — Download de Fatura
==========================================
Faz login no portal Enel com UMA conta, resolve reCAPTCHA v2
(checkbox + desafio de áudio via Whisper) e baixa a fatura atual.

Configuração rápida
-------------------
  1. Preencha EMAIL e SENHA abaixo com as suas credenciais
  2. Ajuste CHROME_VERSION para a versão do seu Chrome
     (chrome://settings/help → número antes do primeiro ponto)
  3. Execute:  python enel_login.py

Dependências
------------
    pip install -r requirements.txt
    # Windows: FFmpeg no PATH  → winget install Gyan.FFmpeg
    # Tesseract OCR            → https://github.com/UB-Mannheim/tesseract/wiki
"""

import os
import time
import logging
import random

import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import recaptcha_audio
import bill_downloader

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÕES — preencha com os seus dados
# ─────────────────────────────────────────────────────────────────────────────
EMAIL  = "iaraavelinoo@gmail.com"   # ← sua conta Enel
SENHA  = "Jon@2104"               # ← sua senha

# Navegador: "chrome" ou "edge"
BROWSER = "edge"

# Página protegida — o portal redireciona para login com sessionDataKey correto.
# Após autenticação, o SAML SSO retorna para cá com sessão válida.
LOGIN_ENTRY_URL = "https://www.enel.com.br/pt-ceara/private-area.html"

# Fallback: URL de login direto (sem sessionDataKey, redireciona para busca.html após auth)
LOGIN_URL_FALLBACK = (
    "https://www.enel.com.br/pt-ceara/login.html"
    "?commonAuthCallerPath=%2Fsamlsso&forceAuth=false&passiveAuth=false"
    "&spEntityID=ENEL_CEA_WEB_BRA&tenantDomain=carbon.super"
    "&relyingParty=ENEL_CEA_WEB_BRA&type=samlsso&sp=ENEL_CEA_WEB_BRA&isSaaSApp=false"
    "&authenticators=FacebookAuthenticator%3Afacebook%3B"
    "GoogleOIDCAuthenticator%3Agoogle%3BOpenIDConnectAuthenticator"
    "%3Aapple_eebrcea%3BEnelCustomBasicAuthenticator%3ALOCAL"
)

CHROME_PROFILE  = r"C:\chromeprofilebot"  # perfil isolado para Chrome
CHROME_VERSION  = 145  # ajuste para a sua versão do Chrome

EDGE_PROFILE    = r"C:\edgeprofilebot"    # perfil isolado para Edge

# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("enel_login.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# DRIVER
# ══════════════════════════════════════════════════════════════════════════════

def create_driver_chrome() -> uc.Chrome:
    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={CHROME_PROFILE}")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "download.default_directory": str(bill_downloader.OUTPUT_DIR.resolve()),
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
    })
    log.info(f"[Driver] Criando Chrome (version_main={CHROME_VERSION})...")
    return uc.Chrome(options=opts, version_main=CHROME_VERSION)


def create_driver_edge() -> webdriver.Edge:
    os.makedirs(EDGE_PROFILE, exist_ok=True)
    opts = EdgeOptions()
    opts.add_argument(f"--user-data-dir={EDGE_PROFILE}")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "download.default_directory": str(bill_downloader.OUTPUT_DIR.resolve()),
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True,
    })
    log.info("[Driver] Criando Edge (selenium-manager gerencia o driver)...")
    driver = webdriver.Edge(options=opts)
    # Remove a flag navigator.webdriver para evitar detecção
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    })
    return driver


def create_driver():
    if BROWSER.lower() == "edge":
        return create_driver_edge()
    return create_driver_chrome()


# ══════════════════════════════════════════════════════════════════════════════
# UTILITÁRIOS DE PÁGINA
# ══════════════════════════════════════════════════════════════════════════════

def dismiss_cookie_banner(driver):
    """Fecha o banner de cookies, se presente."""
    SELECTORS = [
        (By.XPATH, "//button[contains(text(),'Aceitar tudo')]"),
        (By.XPATH, "//button[contains(text(),'Aceitar Tudo')]"),
        (By.XPATH, "//button[contains(text(),'Aceitar')]"),
        (By.CSS_SELECTOR, "button.accept-cookies, button#acceptCookies"),
    ]
    for by, sel in SELECTORS:
        try:
            btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            log.info("[Cookie] Banner dispensado.")
            return
        except TimeoutException:
            continue
    log.debug("[Cookie] Banner não encontrado.")


def _find_element(driver, selectors, timeout=5):
    """Tenta cada seletor e retorna o primeiro elemento visível."""
    wait = WebDriverWait(driver, timeout)
    for by, sel in selectors:
        try:
            return wait.until(EC.visibility_of_element_located((by, sel)))
        except TimeoutException:
            continue
    return None


_SUBMIT_SELS = [
    (By.ID,           "loginButton"),           # WSO2 IS padrão
    (By.CSS_SELECTOR, "input#loginButton"),
    (By.CSS_SELECTOR, "button[type='submit']"),
    (By.CSS_SELECTOR, "input[type='submit']"),
    (By.CSS_SELECTOR, "button.btn-primary, button.login-btn"),
    (By.XPATH, "//button[contains(normalize-space(),'Entrar')]"),
    (By.XPATH, "//button[contains(normalize-space(),'Login')]"),
    (By.XPATH, "//button[contains(normalize-space(),'Continuar')]"),
    (By.XPATH, "//input[@value='Sign In' or @value='Entrar' or @value='Login']"),
    (By.XPATH, "//form//button"),
]


def fill_login_fields(driver, email: str, senha: str):
    """Preenche apenas os campos de e-mail e senha (sem submeter)."""
    driver.switch_to.default_content()
    log.info(f"[Login] Preenchendo campos para: {email}")

    EMAIL_SELS = [
        (By.ID,           "username"),
        (By.NAME,         "username"),
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[name='email']"),
        (By.CSS_SELECTOR, "input[placeholder*='e-mail' i]"),
        (By.CSS_SELECTOR, "input[placeholder*='CPF' i]"),
    ]
    PASS_SELS = [
        (By.ID,           "password"),
        (By.NAME,         "password"),
        (By.CSS_SELECTOR, "input[type='password']"),
    ]

    email_field = _find_element(driver, EMAIL_SELS)
    if not email_field:
        raise NoSuchElementException("Campo de e-mail não encontrado.")
    email_field.clear()
    email_field.send_keys(email)
    time.sleep(random.uniform(0.3, 0.7))

    pass_field = _find_element(driver, PASS_SELS)
    if not pass_field:
        raise NoSuchElementException("Campo de senha não encontrado.")
    pass_field.clear()
    pass_field.send_keys(senha)
    time.sleep(random.uniform(0.3, 0.7))
    log.info("[Login] Campos preenchidos.")
    return pass_field  # retorna para uso como fallback de Enter


def submit_login(driver, pass_field=None):
    """Clica no botão de submit. Fallbacks: JS → Enter no campo senha."""
    driver.switch_to.default_content()

    btn = _find_element(driver, _SUBMIT_SELS, timeout=3)
    if btn:
        btn.click()
        log.info("[Login] Submit via clique no botão.")
        return

    # Fallback 1 — JavaScript
    try:
        driver.execute_script(
            "var f = document.querySelector('form'); if(f) f.submit();"
        )
        log.info("[Login] Submit via JavaScript.")
        return
    except Exception:
        pass

    # Fallback 2 — Enter no campo de senha (ou elemento ativo)
    try:
        target = pass_field or driver.switch_to.active_element
        target.send_keys(Keys.RETURN)
        log.info("[Login] Submit via Enter.")
    except Exception as exc:
        log.warning(f"[Login] Não foi possível submeter: {exc}")


def check_login_success(driver, timeout: int = 8) -> bool:
    """Aguarda e verifica se o login foi bem-sucedido pela URL."""
    time.sleep(timeout)
    url = driver.current_url
    success_kw = [
        "minha-conta", "dashboard", "perfil",
        "account", "area-cliente", "minhas-faturas", "private-area",
    ]
    if any(kw in url.lower() for kw in success_kw):
        log.info(f"[Login] Sucesso! URL: {url}")
        return True
    if "login" in url.lower():
        log.warning(f"[Login] Ainda na página de login. URL: {url}")
        return False
    log.info(f"[Login] URL pós-submit: {url}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run():
    log.info("═" * 55)
    log.info(" Enel Login Automation — Download de Fatura")
    log.info("═" * 55)

    driver = create_driver()
    driver.maximize_window()

    try:
        # ── 1. Navegar para área protegida — portal redireciona para login ────
        log.info(f"[Step 1/4] Abrindo área privada para iniciar fluxo SAML SSO...")
        driver.get(LOGIN_ENTRY_URL)
        time.sleep(6)  # aguarda redirecionamento para a página de login

        current_url = driver.current_url
        log.info(f"[Step 1/4] URL após navegação: {current_url}")

        # Se não redirecionou para login (já autenticado?), vai direto para faturas
        if "login" not in current_url.lower() and "private-area" in current_url.lower():
            log.info("[Step 1/4] Já autenticado — pulando login.")
            bill_downloader.dismiss_lgpd_popup(driver, timeout=5)
            fatura = bill_downloader.download_bill(driver)
            if fatura:
                log.info("═" * 55)
                log.info(f" Fatura salva em: {fatura.resolve()}")
                log.info("═" * 55)
            else:
                log.warning(" Não foi possível baixar a fatura automaticamente.")
            return

        # Se não redirecionou para login, usa o fallback
        if "login" not in current_url.lower():
            log.warning("[Step 1/4] Não redirecionou para login — usando URL de login fallback.")
            driver.get(LOGIN_URL_FALLBACK)
            time.sleep(4)

        dismiss_cookie_banner(driver)
        time.sleep(1)

        # ── 2. Preencher campos (sem submeter ainda) ──────────────────────────
        log.info("[Step 2/4] Preenchendo campos de login...")
        pass_field = fill_login_fields(driver, EMAIL, SENHA)
        time.sleep(1)

        # ── 3. Resolver reCAPTCHA se já estiver na página ────────────────────
        if "recaptcha" in driver.page_source.lower():
            log.info("[Step 3/4] reCAPTCHA detectado — resolvendo antes de submeter...")
            solved = recaptcha_audio.solve(driver)
            if not solved:
                log.error("[Step 3/4] CAPTCHA não resolvido. Abortando.")
                return
            time.sleep(1)
        else:
            log.info("[Step 3/4] reCAPTCHA não detectado.")

        # ── 3b. Submeter o formulário ─────────────────────────────────────────
        log.info("[Step 3b] Submetendo formulário...")
        submit_login(driver, pass_field)
        time.sleep(2)

        # ── 3c. reCAPTCHA pode aparecer pós-submit (segunda verificação) ──────
        if "recaptcha" in driver.page_source.lower():
            log.info("[Step 3c] reCAPTCHA pós-submit detectado — resolvendo...")
            solved = recaptcha_audio.solve(driver)
            if not solved:
                log.error("[Step 3c] CAPTCHA não resolvido. Abortando.")
                return
            time.sleep(1)
            submit_login(driver)
            time.sleep(2)

        # ── 4. Verificar login ────────────────────────────────────────────────
        if not check_login_success(driver):
            log.error("[Step 4/4] Login falhou. Verifique email/senha.")
            return

        # ── 4b. Aguarda sessão autenticada se estabelecer ────────────────────
        # O portal faz trocas OAuth2/OIDC assíncronas em busca.html?search=.
        # Aguardamos até um elemento de usuário aparecer OU 30s (o que vier primeiro).
        log.info("[Step 4b] Aguardando sessão autenticada na página pós-login...")
        _AUTH_NAV_SELS = [
            (By.CSS_SELECTOR, "a[href*='private-area']"),
            (By.CSS_SELECTOR, "a[href*='area-cliente'], a[href*='minhas-faturas']"),
            (By.CSS_SELECTOR, "[class*='user-logged'], [class*='header-user'], [class*='user-menu']"),
            (By.CSS_SELECTOR, "[class*='logged-in'], [data-user], [class*='perfil']"),
            (By.XPATH, "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'minha conta')]"),
            (By.XPATH, "//a[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'área do cliente')]"),
        ]
        _auth_elem = None
        for _by, _sel in _AUTH_NAV_SELS:
            try:
                _auth_elem = WebDriverWait(driver, 5).until(EC.presence_of_element_located((_by, _sel)))
                log.info(f"[Step 4b] Elemento autenticado detectado: {_sel}")
                break
            except TimeoutException:
                continue
        if not _auth_elem:
            log.info("[Step 4b] Nenhum elemento de auth detectado — aguardando 15s adicionais.")
            time.sleep(15)

        # Tenta LGPD popup (pode aparecer com delay JS)
        bill_downloader.dismiss_lgpd_popup(driver, timeout=5)

        # Loga cookies para diagnóstico de sessão
        _cookies = {c["name"]: c.get("domain","") for c in driver.get_cookies()}
        log.info(f"[Step 4b] Cookies ativos: {list(_cookies.keys())}")

        # Tenta navegar para private-area via link na página (SPA navigation)
        try:
            _priv_link = driver.find_element(By.CSS_SELECTOR, "a[href*='private-area']")
            log.info("[Step 4b] Link private-area encontrado na página — clicando.")
            driver.execute_script("arguments[0].click();", _priv_link)
            time.sleep(5)
            log.info(f"[Step 4b] URL após clique SPA: {driver.current_url}")
        except NoSuchElementException:
            log.info("[Step 4b] Link private-area não encontrado na página atual.")

        # ── 5. Baixar fatura ─────────────────────────────────────────────────
        log.info("[Step 4/4] Login OK — iniciando download da fatura...")
        fatura = bill_downloader.download_bill(driver)

        if fatura:
            log.info("═" * 55)
            log.info(f" Fatura salva em: {fatura.resolve()}")
            log.info("═" * 55)
        else:
            log.warning("═" * 55)
            log.warning(" Não foi possível baixar a fatura automaticamente.")
            log.warning(" Verifique o seletor em bill_downloader.py.")
            log.warning("═" * 55)

    except Exception as exc:
        log.exception(f"Erro inesperado: {exc}")
    finally:
        driver.quit()
        log.info("[Driver] Encerrado.")


if __name__ == "__main__":
    run()
