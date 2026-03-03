"""
recaptcha_audio.py
==================
Resolução completa de reCAPTCHA v2 com fallback para desafio de áudio.

Fluxo
-----
  1. Clica no checkbox  →  Selenium (iframe) ou OCR + mousekey
  2. Verifica se já está resolvido (checkbox marcado)
  3. Se surgir desafio visual → muda para áudio (botão headphone)
  4. Obtém áudio:
       a) Baixa URL do <source src="..."> diretamente (primário)
       b) Grava sistema com FFmpeg WASAPI loopback (fallback)
  5. Transcreve com Whisper (modelo "base" por padrão)
  6. Digita resposta no campo e clica em Verify
  7. Repete até MAX_ATTEMPTS em caso de resposta errada (reload challenge)

Dependências
------------
    pip install openai-whisper rapidfuzz pytesseract mousekey
    pip install fast-ctypes-screenshots numpy requests
    # Windows: FFmpeg no PATH  (winget install Gyan.FFmpeg)
    # Tesseract em C:/Program Files/Tesseract-OCR/
"""

import os
import re
import subprocess
import tempfile
import time
import logging
import random

import numpy as np
import pytesseract
import rapidfuzz
import requests

import mousekey
from fast_ctypes_screenshots import ScreenshotOfOneMonitor
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger(__name__)

# ── Configuração ──────────────────────────────────────────────────────────────
TESSERACT_EXE   = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
WHISPER_MODEL   = "base"   # tiny | base | small | medium
FFMPEG_DURATION = 9.0      # segundos de gravação loopback
MAX_ATTEMPTS    = 3        # tentativas máximas no desafio de áudio

pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE

_mkey = mousekey.MouseKey()
_mkey.enable_failsafekill("ctrl+e")

_whisper_model = None  # carregado sob demanda

# ── Seletores CSS/ID ──────────────────────────────────────────────────────────
_SEL_CHECKBOX_IFRAME  = (By.CSS_SELECTOR, "iframe[title='reCAPTCHA']")
_SEL_CHECKBOX_SPAN    = (By.CSS_SELECTOR, "#recaptcha-anchor")
_SEL_CHALLENGE_IFRAME = (By.CSS_SELECTOR,
                         "iframe[src*='bframe'], "
                         "iframe[title*='recaptcha challenge']")
_SEL_AUDIO_BTN        = (By.ID, "recaptcha-audio-button")
_SEL_AUDIO_SRC        = (By.CSS_SELECTOR,
                         "#audio-source source, source[src*='payload']")
_SEL_AUDIO_INPUT      = (By.ID, "audio-response")
_SEL_VERIFY_BTN       = (By.ID, "recaptcha-verify-button")
_SEL_RELOAD_BTN       = (By.ID, "recaptcha-reload-button")
_SEL_ERROR_MSG        = (By.CSS_SELECTOR, ".rc-audiochallenge-error-message")


# ══════════════════════════════════════════════════════════════════════════════
# WHISPER
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_ffmpeg():
    """
    Garante que o comando 'ffmpeg' esteja acessível.
    Estratégia:
      1. Verifica se 'ffmpeg' já está no PATH
      2. Usa imageio-ffmpeg (bundled) e cria um wrapper 'ffmpeg.bat' no mesmo dir,
         para que subprocess.run(["ffmpeg", ...]) funcione normalmente
    """
    import shutil
    if shutil.which("ffmpeg"):
        return  # já disponível

    try:
        import imageio_ffmpeg
        ffmpeg_exe = os.path.abspath(imageio_ffmpeg.get_ffmpeg_exe())
        ffmpeg_dir = os.path.dirname(ffmpeg_exe)

        # Cria ffmpeg.bat wrapper para que 'ffmpeg' funcione como comando
        wrapper = os.path.join(ffmpeg_dir, "ffmpeg.bat")
        if not os.path.exists(wrapper):
            with open(wrapper, "w") as f:
                f.write(f'@echo off\n"{ffmpeg_exe}" %*\n')
            log.info(f"[FFmpeg] Wrapper criado: {wrapper}")

        # Adiciona ao PATH do processo atual
        if ffmpeg_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

        log.info(f"[FFmpeg] Bundled disponível: {ffmpeg_exe}")
    except Exception as exc:
        log.warning(f"[FFmpeg] imageio-ffmpeg não disponível: {exc}. "
                    "Instale com: winget install Gyan.FFmpeg")


def _load_whisper():
    """Carrega o modelo Whisper uma única vez (lazy singleton)."""
    global _whisper_model
    _ensure_ffmpeg()  # garante FFmpeg antes de transcrever
    if _whisper_model is None:
        import whisper  # importação tardia — evita custo se não for usado
        log.info(f"[Whisper] Carregando modelo '{WHISPER_MODEL}'...")
        _whisper_model = whisper.load_model(WHISPER_MODEL)
        log.info("[Whisper] Modelo pronto.")
    return _whisper_model


def _transcribe(audio_path: str) -> str:
    """
    Transcreve um arquivo de áudio com Whisper.
    Retorna a resposta em lowercase sem espaços/pontuação.
    """
    model = _load_whisper()
    log.info(f"[Whisper] Transcrevendo: {audio_path}")
    result = model.transcribe(audio_path, language="en", fp16=False)
    raw = result["text"].strip()
    log.info(f"[Whisper] Texto bruto   : '{raw}'")
    clean = re.sub(r"[^0-9a-zA-Z]", "", raw).lower()
    log.info(f"[Whisper] Resposta limpa: '{clean}'")
    return clean


# ══════════════════════════════════════════════════════════════════════════════
# CAPTURA DE ÁUDIO
# ══════════════════════════════════════════════════════════════════════════════

def _extract_audio_url(driver, timeout: float = 10.0) -> "str | None":
    """
    Extrai a URL do áudio do desafio com polling.
    Aguarda até `timeout` segundos para o audio challenge carregar após o clique.
    Deve ser chamado com o driver já dentro do challenge iframe.
    """
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1

        # Abordagem 1 — JavaScript (evita crash em iframe cross-origin)
        try:
            url = driver.execute_script("""
                try {
                    // Seletores específicos do reCAPTCHA
                    var src = document.querySelector('#audio-source source');
                    if (src && src.src) return src.src;
                    var audio = document.querySelector('#audio-source');
                    if (audio && audio.src) return audio.src;
                    var link = document.querySelector('.rc-audiochallenge-tdownload-link');
                    if (link && link.href) return link.href;
                    // Qualquer source/audio com payload do reCAPTCHA
                    var anyAudio = document.querySelector('audio source, audio[src]');
                    if (anyAudio) return anyAudio.src || anyAudio.getAttribute('src');
                    // Broadest: qualquer href/src com 'payload' ou 'recaptcha'
                    var all = document.querySelectorAll('[src*="payload"],[href*="payload"],[src*="recaptcha/api2"],[href*="recaptcha/api2"]');
                    for (var i=0; i < all.length; i++) {
                        var u = all[i].src || all[i].href;
                        if (u && u.includes('http')) return u;
                    }
                } catch(e) {}
                return null;
            """)
            if url:
                log.info(f"[Áudio-DL] URL via JS (tentativa {attempt}): {url[:80]}...")
                return url
        except Exception as exc:
            log.debug(f"[Áudio-DL] JS tentativa {attempt} falhou: {exc}")

        # Abordagem 2 — link de download
        try:
            dl = driver.find_element(
                By.CSS_SELECTOR, "a.rc-audiochallenge-tdownload-link"
            )
            url = dl.get_attribute("href") or ""
            if url:
                log.info(f"[Áudio-DL] URL via link (tentativa {attempt}): {url[:80]}...")
                return url
        except Exception:
            pass

        # Abordagem 3 — <source>
        try:
            src = driver.find_element(By.CSS_SELECTOR, "#audio-source source")
            url = src.get_attribute("src") or ""
            if url:
                log.info(f"[Áudio-DL] URL via <source> (tentativa {attempt}): {url[:80]}...")
                return url
        except Exception:
            pass

        time.sleep(0.7)

    log.warning(f"[Áudio-DL] URL não encontrada após {timeout}s de polling.")
    return None


def _download_audio(driver) -> "str | None":
    """
    Estratégia primária: extrai URL do áudio do iframe e baixa via requests.
    Retorna o caminho do arquivo temporário ou None.
    """
    url = _extract_audio_url(driver)
    if not url:
        log.warning("[Áudio-DL] Não foi possível extrair a URL do áudio.")
        return None
    try:
        # Cookies do contexto principal (não do iframe)
        driver.switch_to.default_content()
        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        ua = driver.execute_script("return navigator.userAgent")
        # Volta para o iframe após pegar os cookies
        driver.switch_to.default_content()
        WebDriverWait(driver, 5).until(
            EC.frame_to_be_available_and_switch_to_it(_SEL_CHALLENGE_IFRAME)
        )

        resp = requests.get(
            url,
            cookies=cookies,
            headers={"User-Agent": ua, "Referer": "https://www.google.com/"},
            timeout=15,
        )
        resp.raise_for_status()

        ext = ".mp3" if "mp3" in resp.headers.get("Content-Type", "") else ".wav"
        path = tempfile.mktemp(suffix=ext)
        with open(path, "wb") as f:
            f.write(resp.content)

        log.info(f"[Áudio-DL] {len(resp.content)} bytes → {path}")
        return path

    except Exception as exc:
        log.warning(f"[Áudio-DL] Download falhou: {exc}")
        return None


def _record_pyaudio(duration: float = FFMPEG_DURATION) -> "str | None":  # noqa: C901
    """
    Grava o áudio do sistema via pyaudiowpatch (WASAPI loopback — Python nativo).
    Não requer FFmpeg instalado.
    pip install pyaudiowpatch
    """
    try:
        import pyaudiowpatch as pyaudio
        import wave
    except ImportError:
        log.debug("[PyAudio] pyaudiowpatch não instalado.")
        return None

    try:
        path = tempfile.mktemp(suffix=".wav")
        CHUNK = 512

        with pyaudio.PyAudio() as p:
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])

            # Procura o dispositivo de loopback correspondente
            loopback_dev = None
            for lb in p.get_loopback_device_info_generator():
                if default_out["name"] in lb["name"]:
                    loopback_dev = lb
                    break

            if not loopback_dev:
                log.warning("[PyAudio] Dispositivo de loopback não encontrado.")
                return None

            rate = int(loopback_dev["defaultSampleRate"])
            channels = loopback_dev["maxInputChannels"]
            log.info(f"[PyAudio] Gravando {duration}s via WASAPI loopback "
                     f"({loopback_dev['name']})...")

            frames = []
            stream = p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                frames_per_buffer=CHUNK,
                input=True,
                input_device_index=loopback_dev["index"],
            )
            # Thread com timeout para não bloquear indefinidamente
            import threading
            stop_flag = threading.Event()
            n_chunks = int(rate / CHUNK * duration)

            def _read_loop():
                for _ in range(n_chunks):
                    if stop_flag.is_set():
                        break
                    try:
                        frames.append(stream.read(CHUNK, exception_on_overflow=False))
                    except Exception:
                        break

            reader = threading.Thread(target=_read_loop, daemon=True)
            reader.start()
            reader.join(timeout=duration + 5)
            if reader.is_alive():
                stop_flag.set()
                log.warning("[PyAudio] Timeout — encerrando stream.")

            stream.stop_stream()
            stream.close()

        with wave.open(path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # paInt16 = 2 bytes
            wf.setframerate(rate)
            wf.writeframes(b"".join(frames))

        log.info(f"[PyAudio] Gravado → {path}")
        return path

    except Exception as exc:
        log.warning(f"[PyAudio] Falhou: {exc}")
        return None


def _record_ffmpeg(duration: float = FFMPEG_DURATION) -> "str | None":
    """
    Captura o áudio do sistema via FFmpeg WASAPI loopback.
    Pré-requisito: winget install Gyan.FFmpeg
    """
    path = tempfile.mktemp(suffix=".wav")
    cmd = [
        "ffmpeg", "-y",
        "-f", "wasapi",
        "-loopback",
        "-i", "default",
        "-t", str(duration),
        "-ar", "16000",
        "-ac", "1",
        path,
    ]
    log.info(f"[FFmpeg] Gravando {duration}s via WASAPI loopback...")
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=duration + 15)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace")[-400:]
            log.warning(f"[FFmpeg] Erro (code {proc.returncode}): {err}")
            return None
        log.info(f"[FFmpeg] Gravado → {path}")
        return path
    except FileNotFoundError:
        log.warning("[FFmpeg] 'ffmpeg' não encontrado. "
                    "Instale com: winget install Gyan.FFmpeg")
        return None
    except Exception as exc:
        log.warning(f"[FFmpeg] Exceção: {exc}")
        return None


def _record_system_audio(duration: float = FFMPEG_DURATION) -> "str | None":
    """
    Tenta gravar o áudio do sistema em cascata:
      1. pyaudiowpatch (Python nativo, sem dependência externa)
      2. FFmpeg WASAPI loopback
    """
    path = _record_pyaudio(duration)
    if path:
        return path
    return _record_ffmpeg(duration)


def _click_play_button(driver):
    """Clica no botão de play do desafio de áudio para iniciar reprodução."""
    try:
        play_btn = driver.find_element(
            By.CSS_SELECTOR,
            "button.rc-button-audio.goog-inline-block, "
            "button[aria-labelledby='audio-instructions']",
        )
        play_btn.click()
        log.info("[Áudio] Botão play clicado.")
        time.sleep(0.8)
    except Exception:
        log.debug("[Áudio] Botão play não encontrado.")


def _reenter_challenge_iframe(driver) -> bool:
    """Re-entra no challenge iframe (necessário após o iframe renavegar)."""
    driver.switch_to.default_content()
    try:
        WebDriverWait(driver, 6).until(
            EC.frame_to_be_available_and_switch_to_it(_SEL_CHALLENGE_IFRAME)
        )
        return True
    except TimeoutException:
        log.warning("[Challenge] Não foi possível re-entrar no iframe.")
        return False


def _get_audio_answer(driver) -> "str | None":
    """
    Obtém a resposta do desafio em cascata:
      1. Re-entra no iframe (garante contexto válido após audio button click)
      2. Download direto da URL do áudio
      3. Gravação do sistema (pyaudiowpatch → FFmpeg)
    """
    # Sempre re-entra no iframe antes de tentar extrair a URL
    # (o iframe pode ter renavegado após clicar no botão de áudio)
    _reenter_challenge_iframe(driver)

    # Estratégia 1 — download direto
    audio_path = _download_audio(driver)

    # Estratégia 2 — gravação do sistema
    if not audio_path:
        log.info("[Áudio] Tentando gravação do sistema...")
        # Re-entra no iframe para clicar no play
        _reenter_challenge_iframe(driver)
        _click_play_button(driver)
        audio_path = _record_system_audio()

    if not audio_path or not os.path.exists(audio_path):
        log.error("[Áudio] Nenhum áudio obtido.")
        return None

    # Verifica tamanho mínimo (evita segfault do Whisper com WAV vazio)
    size = os.path.getsize(audio_path)
    if size < 4096:
        log.warning(f"[Áudio] Arquivo muito pequeno ({size} bytes), ignorando.")
        try:
            os.remove(audio_path)
        except Exception:
            pass
        return None

    try:
        return _transcribe(audio_path)
    finally:
        try:
            os.remove(audio_path)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# SCREENSHOT / OCR — fallback para o checkbox
# ══════════════════════════════════════════════════════════════════════════════

def _screenshot_ocr(minlen: int = 2):
    import pandas as pd
    with ScreenshotOfOneMonitor(monitor=0, ascontiguousarray=True) as mon:
        img = mon.screenshot_one_monitor()
    df = pytesseract.image_to_data(img, output_type="data.frame")
    df = df.dropna(subset=["text"])
    return df.loc[df["text"].str.len() > minlen].reset_index(drop=True)


def _human_click(x: int, y: int):
    _mkey.left_click_xy_natural(
        int(x) + random.randint(-8, 8),
        int(y) + random.randint(-8, 8),
        delay=random.uniform(0.18, 0.32),
        min_variation=-8,
        max_variation=8,
        use_every=4,
        sleeptime=(0.009, 0.019),
        print_coords=True,
        percent=90,
    )


# ══════════════════════════════════════════════════════════════════════════════
# CHECKBOX
# ══════════════════════════════════════════════════════════════════════════════

def _click_checkbox_selenium(driver) -> bool:
    """Troca para o iframe do reCAPTCHA e clica no span#recaptcha-anchor."""
    from selenium.common.exceptions import ElementClickInterceptedException
    log.info("[Checkbox] Tentando via Selenium...")
    try:
        driver.switch_to.default_content()
        _scroll_captcha_into_view(driver)
        wait = WebDriverWait(driver, 10)
        wait.until(EC.frame_to_be_available_and_switch_to_it(_SEL_CHECKBOX_IFRAME))
        elem = wait.until(EC.presence_of_element_located(_SEL_CHECKBOX_SPAN))
        # Tenta clique direto primeiro, fallback para JS click
        try:
            elem.click()
        except ElementClickInterceptedException:
            log.debug("[Checkbox] Clique interceptado — usando JS click.")
            driver.execute_script("arguments[0].click();", elem)
        log.info("[Checkbox] Clicado via Selenium.")
        return True
    except TimeoutException:
        log.warning("[Checkbox] iframe/span não encontrado pelo Selenium.")
        return False
    finally:
        driver.switch_to.default_content()


def _click_checkbox_ocr(driver) -> bool:
    """
    Fallback: faz screenshot da tela, localiza 'I'm not a robot'
    via Tesseract + fuzzy match e clica com trajetória humana.
    """
    import pandas as pd
    log.info("[Checkbox] Fallback OCR + mousekey...")
    df = _screenshot_ocr()
    scores = pd.DataFrame(
        rapidfuzz.process_cpp.cdist(["Imnot", "arobot"], df["text"].to_list())
    ).T.rename(columns={0: "imnot", 1: "arobot"})
    df = pd.concat([df, scores], axis=1)

    try:
        cond = (
            ((df["imnot"] == df["imnot"].max()) & (df["imnot"] > 90)) |
            ((df["arobot"] == df["arobot"].max()) & (df["arobot"] > 90))
        )
        matched = df.loc[cond]
        ok = len(matched) >= 2 and (np.diff(matched[:2].index)[0] == 1)
    except Exception:
        ok = False

    if ok:
        x, y = df.loc[df["imnot"] == df["imnot"].max()][["left", "top"]].values[0]
        log.info(f"[Checkbox-OCR] Clicando em ({x}, {y})")
        _human_click(int(x), int(y))
        return True

    log.error("[Checkbox-OCR] Texto do checkbox não encontrado na tela.")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# ESTADO DO CAPTCHA
# ══════════════════════════════════════════════════════════════════════════════

def _is_solved(driver) -> bool:
    """Verifica se o checkbox está com aria-checked='true'."""
    try:
        driver.switch_to.default_content()
        WebDriverWait(driver, 3).until(
            EC.frame_to_be_available_and_switch_to_it(_SEL_CHECKBOX_IFRAME)
        )
        anchor = driver.find_element(*_SEL_CHECKBOX_SPAN)
        return anchor.get_attribute("aria-checked") == "true"
    except Exception:
        return False
    finally:
        driver.switch_to.default_content()


# ══════════════════════════════════════════════════════════════════════════════
# DESAFIO DE ÁUDIO — navegação no iframe
# ══════════════════════════════════════════════════════════════════════════════

def _scroll_captcha_into_view(driver):
    """
    Garante que os elementos do reCAPTCHA estejam no viewport antes de interagir.
    Tenta tanto o iframe do desafio quanto o do checkbox, e rola até eles.
    """
    driver.switch_to.default_content()
    for sel in [_SEL_CHALLENGE_IFRAME, _SEL_CHECKBOX_IFRAME]:
        try:
            elem = driver.find_element(*sel)
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});",
                elem,
            )
            log.debug("[Scroll] reCAPTCHA rolado para o centro da tela.")
            time.sleep(0.4)
            return
        except Exception:
            continue
    # Fallback: rola para o topo
    try:
        driver.execute_script("window.scrollTo(0, 0);")
        log.debug("[Scroll] Página rolada para o topo.")
        time.sleep(0.3)
    except Exception:
        pass


def _enter_challenge_iframe(driver) -> bool:
    """
    Troca o contexto do driver para o iframe do desafio e aguarda
    até que haja conteúdo interativo dentro dele (polling).
    """
    driver.switch_to.default_content()
    _scroll_captcha_into_view(driver)

    # Loga todos os iframes presentes para debug
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for fr in iframes:
            log.debug(
                f"[Challenge] iframe encontrado — "
                f"src={fr.get_attribute('src') or ''}[:60] "
                f"title={fr.get_attribute('title') or ''}"
            )
    except Exception:
        pass

    # Rola o iframe de desafio para o centro da tela
    try:
        iframe_elem = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(_SEL_CHALLENGE_IFRAME)
        )
        src = iframe_elem.get_attribute("src") or ""
        title = iframe_elem.get_attribute("title") or ""
        log.info(f"[Challenge] Iframe encontrado — title='{title}' src='{src[:60]}'")
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'instant', block:'center'});",
            iframe_elem,
        )
        time.sleep(0.5)
    except Exception:
        pass

    # Entra no iframe
    try:
        WebDriverWait(driver, 8).until(
            EC.frame_to_be_available_and_switch_to_it(_SEL_CHALLENGE_IFRAME)
        )
    except TimeoutException:
        log.warning("[Challenge] Iframe do desafio não encontrado.")
        return False

    # Polling: aguarda botões OU elementos interativos aparecerem (até 30 segundos)
    log.info("[Challenge] Aguardando renderização do conteúdo do iframe...")
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            result = driver.execute_script("""
                var btns = document.querySelectorAll('button').length;
                var audio = document.querySelector('#recaptcha-audio-button') ? 1 : 0;
                var img = document.querySelectorAll('.rc-imageselect-target img').length;
                return btns + audio + img;
            """)
            if result and result > 0:
                btns = driver.execute_script("return document.querySelectorAll('button').length;")
                log.info(f"[Challenge] Iframe ativo com {btns} botão(ões).")
                return True
        except Exception:
            pass
        time.sleep(0.5)

    # Loga o HTML do iframe para debug (primeiros 500 chars)
    try:
        html = driver.execute_script("return document.body.innerHTML;") or ""
        log.warning(f"[Challenge] Iframe vazio após polling. HTML: {html[:300]}")
    except Exception:
        pass

    log.warning("[Challenge] Iframe do desafio ativado mas sem conteúdo.")
    return True  # retorna True mesmo sem botões para tentar JS fallback


_AUDIO_BTN_SELECTORS = [
    (By.ID,           "recaptcha-audio-button"),
    (By.CSS_SELECTOR, "button#recaptcha-audio-button"),
    (By.CSS_SELECTOR, "button.rc-button-audio"),
    (By.CSS_SELECTOR, "button[aria-label*='audio' i]"),
    (By.CSS_SELECTOR, "button[title*='audio' i]"),
    (By.XPATH,        "//button[contains(@aria-label,'udio')]"),   # audio/áudio
    (By.XPATH,        "//button[@id='recaptcha-audio-button']"),
]


def _click_audio_button(driver) -> bool:
    """
    Clica no ícone de headphone para trocar para desafio de áudio.
    Tenta múltiplos seletores e usa JavaScript como último recurso.
    """
    time.sleep(1.0)  # aguarda renderização do challenge

    # Tenta seletores em sequência
    for by, sel in _AUDIO_BTN_SELECTORS:
        try:
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            log.info(f"[Challenge] Mudado para áudio via seletor: {sel}")
            time.sleep(2.5)  # aguarda iframe recarregar com conteúdo de áudio
            return True
        except TimeoutException:
            continue

    # Fallback — JavaScript: procura botão com id/aria contendo "audio"
    try:
        clicked = driver.execute_script("""
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                var b = btns[i];
                var id = (b.id || '').toLowerCase();
                var aria = (b.getAttribute('aria-label') || '').toLowerCase();
                var title = (b.getAttribute('title') || '').toLowerCase();
                if (id.includes('audio') || aria.includes('audio') || title.includes('audio')) {
                    b.click();
                    return b.id || b.className || 'clicado';
                }
            }
            return null;
        """)
        if clicked:
            log.info(f"[Challenge] Mudado para áudio via JS (elem: {clicked}).")
            time.sleep(2.5)
            return True
    except Exception as exc:
        log.debug(f"[Challenge] JS fallback falhou: {exc}")

    # Debug — loga todos os botões disponíveis no iframe
    try:
        btns_info = driver.execute_script("""
            return Array.from(document.querySelectorAll('button')).map(function(b){
                return {id: b.id, cls: b.className, aria: b.getAttribute('aria-label')};
            });
        """)
        log.warning(f"[Challenge] Botões disponíveis no iframe: {btns_info}")
    except Exception:
        pass

    log.warning("[Challenge] Botão de áudio não encontrado.")
    return False


def _reload_challenge(driver):
    """Recarrega o desafio de áudio clicando no botão de reload."""
    try:
        driver.find_element(*_SEL_RELOAD_BTN).click()
        log.info("[Challenge] Desafio recarregado.")
        time.sleep(1.5)
    except Exception:
        pass


def _submit_answer(driver, answer: str) -> bool:
    """Digita a resposta caractere a caractere e clica em Verify."""
    try:
        inp = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable(_SEL_AUDIO_INPUT)
        )
        inp.clear()
        for ch in answer:
            inp.send_keys(ch)
            time.sleep(random.uniform(0.06, 0.16))

        log.info(f"[Challenge] Resposta digitada: '{answer}'")
        driver.find_element(*_SEL_VERIFY_BTN).click()
        log.info("[Challenge] Verify clicado.")
        return True
    except Exception as exc:
        log.error(f"[Challenge] Erro ao submeter: {exc}")
        return False


def _has_error(driver) -> bool:
    """Verifica se o reCAPTCHA exibiu mensagem de erro (resposta errada)."""
    try:
        msg = driver.find_element(*_SEL_ERROR_MSG)
        return msg.is_displayed() and bool(msg.text.strip())
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# PONTO DE ENTRADA PÚBLICO
# ══════════════════════════════════════════════════════════════════════════════

def solve(driver) -> bool:
    """
    Resolve reCAPTCHA v2 completo.

    Estratégia
    ----------
    1. Checkbox → Selenium (iframe) → OCR + mousekey
    2. Se desafio aparecer → áudio
         a) download direto da URL do <source>
         b) FFmpeg WASAPI loopback  (fallback)
    3. Transcreve com Whisper → digita → Verify
    4. Repete até MAX_ATTEMPTS

    Retorna True se resolvido, False caso contrário.
    """
    driver.switch_to.default_content()

    # Garante que o reCAPTCHA esteja visível independente de scroll anterior
    _scroll_captcha_into_view(driver)

    # ── Passo 1: Clicar no checkbox ───────────────────────────────────────────
    log.info("═" * 50)
    log.info("[CAPTCHA] Passo 1 — Clicando no checkbox")
    log.info("═" * 50)

    clicked = _click_checkbox_selenium(driver) or _click_checkbox_ocr(driver)
    if not clicked:
        log.error("[CAPTCHA] Não foi possível clicar no checkbox.")
        return False

    time.sleep(random.uniform(2.0, 3.5))

    # ── Passo 2: Verificar se já está resolvido ───────────────────────────────
    if _is_solved(driver):
        log.info("[CAPTCHA] Resolvido apenas com o clique no checkbox!")
        return True

    # ── Passo 3: Mudar para desafio de áudio ──────────────────────────────────
    log.info("═" * 50)
    log.info("[CAPTCHA] Passo 2 — Ativando desafio de áudio")
    log.info("═" * 50)

    if not _enter_challenge_iframe(driver):
        return False

    if not _click_audio_button(driver):
        return False

    time.sleep(1.5)

    # ── Passo 4: Loop de tentativas ───────────────────────────────────────────
    for attempt in range(1, MAX_ATTEMPTS + 1):
        log.info("═" * 50)
        log.info(f"[CAPTCHA] Passo 3 — Tentativa de áudio {attempt}/{MAX_ATTEMPTS}")
        log.info("═" * 50)

        answer = _get_audio_answer(driver)
        if not answer:
            log.warning("[CAPTCHA] Sem resposta de áudio. Recarregando...")
            _reload_challenge(driver)
            time.sleep(2)
            continue

        if not _submit_answer(driver, answer):
            continue

        time.sleep(random.uniform(2.5, 4.0))

        # Verifica resultado
        driver.switch_to.default_content()
        if _is_solved(driver):
            log.info(f"[CAPTCHA] Resolvido na tentativa {attempt}!")
            return True

        # Volta para o iframe para verificar erro e recarregar
        if _enter_challenge_iframe(driver):
            if _has_error(driver):
                log.warning(f"[CAPTCHA] Resposta '{answer}' incorreta. Recarregando...")
            _reload_challenge(driver)
            time.sleep(2)

    log.error(f"[CAPTCHA] Falha após {MAX_ATTEMPTS} tentativas de áudio.")
    return False
