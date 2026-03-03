# Enel Login Automation

Automação de login no portal **Enel Ceará**: faz autenticação (SAML SSO), resolve reCAPTCHA v2 (checkbox + desafio de áudio via Whisper) e baixa a fatura atual em PDF.

---

## Índice

- [Funcionalidades](#funcionalidades)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Pré-requisitos](#pré-requisitos)
- [Configuração](#configuração)
- [Uso](#uso)
- [Fluxo técnico](#fluxo-técnico)
- [Resolução do reCAPTCHA](#resolução-do-recaptcha)
- [Download da fatura](#download-da-fatura)
- [Saídas e logs](#saídas-e-logs)
- [Segurança e arquivos ignorados](#segurança-e-arquivos-ignorados)
- [Solução de problemas](#solução-de-problemas)
- [Possíveis extensões](#possíveis-extensões)

---

## Funcionalidades

- **Login automático** no portal Enel Ceará (área do cliente) com e-mail e senha.
- **Bypass de reCAPTCHA v2**: clique no checkbox (Selenium ou OCR + movimento de mouse) e resolução do desafio de áudio com **Whisper** (transcrição de áudio).
- **Download da fatura** em PDF para a pasta `faturas/` após o login.
- Suporte a **Chrome** (undetected-chromedriver) ou **Edge** (Selenium nativo).
- Credenciais via arquivo **`.env`** (não versionado).
- Logs em console e em arquivo (`enel_login.log`).

---

## Estrutura do projeto

```
enel_login_automation/
├── enel_login.py          # Script principal: login + reCAPTCHA + orquestração
├── recaptcha_audio.py     # Resolução reCAPTCHA v2 (checkbox + áudio + Whisper)
├── bill_downloader.py     # Navegação na área autenticada e download do PDF da fatura
├── gerar_credenciais.py   # Gera Excel de exemplo (credenciais.xlsx) — opcional
├── requirements.txt       # Dependências Python
├── .env.example           # Modelo de variáveis de ambiente (copiar para .env)
├── .env                   # Suas credenciais (criar a partir do .env.example, NÃO versionar)
├── .gitignore
└── README.md
```

Após a execução:

- **`faturas/`** — PDFs das faturas baixadas (criada automaticamente).
- **`enel_login.log`** — Log da última execução (não versionado).

---

## Pré-requisitos

### 1. Python 3.9+

Recomendado usar um ambiente virtual:

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# ou: source .venv/bin/activate  # Linux/macOS
```

### 2. Dependências Python

```bash
pip install -r requirements.txt
```

Principais pacotes: `undetected-chromedriver`, `selenium`, `openai-whisper`, `pytesseract`, `rapidfuzz`, `mousekey`, `fast-ctypes-screenshots`, `python-dotenv`, `requests`, `pandas`, `openpyxl`.

### 3. Tesseract OCR

Usado no fallback do checkbox do reCAPTCHA (localização do texto na tela).

- **Download**: [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
- **Windows**: instalar em `C:\Program Files\Tesseract-OCR\` (caminho padrão usado no código).
- **Linux**: `sudo apt install tesseract-ocr` (e ajustar `TESSERACT_EXE` em `recaptcha_audio.py` se necessário).

### 4. FFmpeg

Usado para obter o áudio do desafio reCAPTCHA (gravação loopback quando a URL direta não estiver disponível).

- **Windows**: `winget install Gyan.FFmpeg` e garantir que `ffmpeg` esteja no `PATH`.
- **Linux**: `sudo apt install ffmpeg`.

### 5. Navegador: Chrome ou Edge

- **Chrome**: instalação normal; o `undetected-chromedriver` baixa o driver compatível com a versão do Chrome.
- **Edge**: instalação normal; o Selenium usa o driver gerenciado automaticamente (selenium-manager).

A versão do Chrome pode ser informada na variável `CHROME_VERSION` no `.env` (número antes do primeiro ponto em `chrome://settings/help`).

---

## Configuração

### 1. Variáveis de ambiente (obrigatório)

Copie o exemplo e preencha com suas credenciais:

```bash
copy .env.example .env   # Windows
# ou: cp .env.example .env   # Linux/macOS
```

Edite o arquivo **`.env`**:

```env
# Credenciais do portal Enel (obrigatório)
ENEL_EMAIL=seu_email@exemplo.com
ENEL_SENHA=sua_senha

# Navegador: "chrome" ou "edge" (padrão: edge)
BROWSER=edge

# Versão do Chrome (apenas se BROWSER=chrome). Ver em chrome://settings/help
CHROME_VERSION=145
```

**Importante:** o arquivo `.env` não deve ser commitado (já está no `.gitignore`).

### 2. Ajustes opcionais no código

Em **`enel_login.py`**:

- `CHROME_PROFILE` — pasta de perfil do Chrome (padrão: `C:\chromeprofilebot`).
- `EDGE_PROFILE` — pasta de perfil do Edge (padrão: `C:\edgeprofilebot`).
- `LOGIN_ENTRY_URL` / `LOGIN_URL_FALLBACK` — URLs do portal (normalmente não é necessário alterar).

Em **`recaptcha_audio.py`**:

- `TESSERACT_EXE` — caminho do executável do Tesseract.
- `WHISPER_MODEL` — modelo Whisper: `tiny`, `base`, `small`, `medium` (maior = mais preciso, mais lento).
- `FFMPEG_DURATION` — duração em segundos da gravação de áudio (fallback).
- `MAX_ATTEMPTS` — tentativas máximas no desafio de áudio.

Em **`bill_downloader.py`**:

- `OUTPUT_DIR` — pasta onde os PDFs são salvos (padrão: `faturas`).

---

## Uso

### Execução rápida

1. Configure o `.env` com `ENEL_EMAIL` e `ENEL_SENHA`.
2. Execute:

```bash
python enel_login.py
```

O script irá:

1. Abrir o navegador (Chrome ou Edge, conforme `BROWSER`).
2. Acessar a área privada do portal Enel (redirecionamento para a página de login).
3. Fechar o banner de cookies, se aparecer.
4. Preencher e-mail e senha.
5. Resolver o reCAPTCHA v2 (checkbox e, se surgir, desafio de áudio com Whisper).
6. Submeter o formulário e aguardar o login.
7. Fechar o popup LGPD (se aparecer).
8. Navegar até a área de faturas e baixar o PDF da fatura na pasta `faturas/`.

### Template de credenciais em Excel (opcional)

Se quiser usar o arquivo Excel apenas como modelo (o script principal usa `.env`):

```bash
python gerar_credenciais.py
```

Isso cria `credenciais.xlsx` com colunas `email` e `senha`. Você pode editá-lo para referência; o login automático atual lê apenas o `.env`.

---

## Fluxo técnico

1. **Entrada**: acessa `LOGIN_ENTRY_URL` (área privada). O portal redireciona para a página de login com SAML SSO.
2. **Cookies**: dispensa o banner de cookies.
3. **Formulário**: preenche usuário e senha (sem submeter).
4. **reCAPTCHA**: se o reCAPTCHA estiver na página, resolve antes de submeter (checkbox + áudio se necessário).
5. **Submit**: envia o formulário. Se o reCAPTCHA aparecer de novo após o submit, resolve novamente.
6. **Pós-login**: verifica sucesso pela URL; aguarda elementos da área autenticada; dispensa popup LGPD.
7. **Fatura**: chama `bill_downloader.download_bill()` para localizar e baixar o PDF.

---

## Resolução do reCAPTCHA

O módulo **`recaptcha_audio`** implementa:

1. **Checkbox “Não sou um robô”**
   - **Primário**: Selenium entra no iframe do reCAPTCHA e clica em `#recaptcha-anchor`.
   - **Fallback**: OCR (Tesseract) localiza o texto na tela e o **mousekey** simula um clique com movimento em curva (Bezier) para parecer mais humano.

2. **Desafio de áudio** (quando o checkbox não basta)
   - Troca para o desafio de áudio (botão de fone).
   - Obtém o áudio: primeiro pela URL do `<source src="...">`; se não der, grava o áudio do sistema com FFmpeg (WASAPI loopback no Windows).
   - Transcreve com **Whisper** (modelo configurável, ex.: `base`).
   - Digita a resposta no campo e clica em “Verify”.
   - Até `MAX_ATTEMPTS` tentativas em caso de erro (ex.: “Try again”).

O uso de movimento de mouse natural e Whisper local reduz a dependência de serviços externos de CAPTCHA.

---

## Download da fatura

O **`bill_downloader`**:

1. Usa a sessão já autenticada no navegador.
2. Navega para as URLs de “Minhas Faturas” / “Segunda via” (ou segue links do menu).
3. Aguarda o carregamento do conteúdo dinâmico.
4. Localiza o botão/link de download da fatura (vários seletores, do mais específico ao genérico).
5. Ignora links de privacidade, termos, cookies, etc.
6. Faz scroll até o elemento, clica e aguarda o download na pasta configurada (`OUTPUT_DIR`).
7. Fallback: se houver URL direta do PDF, pode baixar via `requests` reutilizando cookies.

Os PDFs ficam em **`faturas/`** (ou no diretório definido em `OUTPUT_DIR`).

---

## Saídas e logs

| Saída            | Descrição |
|------------------|-----------|
| `faturas/*.pdf`  | Faturas baixadas (nome/estrutura conforme o portal). |
| `enel_login.log` | Log da execução (nível INFO, encoding UTF-8). |

O log inclui passos do login, reCAPTCHA, navegação e download. Em caso de falha, verifique o trecho correspondente no log.

---

## Segurança e arquivos ignorados

- **`.env`** — contém e-mail e senha; não deve ser versionado.
- **`credenciais.xlsx`** — se usado, pode conter dados sensíveis; está no `.gitignore`.
- **`faturas/`** — PDFs das suas contas; não versionados.
- **`*.log`** — podem conter URLs e dados de sessão.
- **Perfis de navegador** (`C:\chromeprofilebot\`, `C:\edgeprofilebot\`) — cookies e sessão; listados no `.gitignore`.

Mantenha o `.env` apenas na sua máquina e nunca o envie em repositórios ou canais inseguros.

---

## Solução de problemas

### “Campo de e-mail não encontrado” / “Campo de senha não encontrado”

- O layout do portal pode ter mudado. Verifique se a página de login carregou por completo (e se não caiu em outra URL).
- Confirme que `ENEL_EMAIL` e `ENEL_SENHA` estão corretos no `.env`.

### reCAPTCHA não resolvido

- **Tesseract**: confirme que está instalado e que `TESSERACT_EXE` em `recaptcha_audio.py` aponta para o executável correto.
- **Whisper**: na primeira execução o modelo é baixado (ex.: `base`). Verifique conexão e espaço em disco.
- **FFmpeg**: necessário para o fallback de áudio; verifique se `ffmpeg` está no `PATH`.

### Chrome/Edge não abre ou dá erro de driver

- **Chrome**: confirme a versão em `chrome://settings/help` e ajuste `CHROME_VERSION` no `.env`.
- **Edge**: em geral o Selenium gerencia o driver; atualize `selenium` e o Edge.
- Feche outras instâncias do navegador que estejam usando o mesmo perfil (`CHROME_PROFILE` / `EDGE_PROFILE`).

### Fatura não baixa

- O seletor do botão de download pode ter mudado. Veja `bill_downloader.py`, lista `_BILL_LINK_SELECTORS`, e o HTML da página “Minhas Faturas”.
- Verifique em `enel_login.log` se o script chegou à etapa de download e se há mensagens de timeout ou elemento não encontrado.

### Erro de import (Whisper, Tesseract, etc.)

- Ative o mesmo ambiente virtual onde rodou `pip install -r requirements.txt`.
- No Windows, às vezes é necessário reinstalar o **Visual C++ Redistributable** para bibliotecas nativas usadas pelo Whisper.

---

## Possíveis extensões

- **reCAPTCHA v3**: se o site migrar para v3, considerar serviços como 2Captcha ou CapSolver.
- **Modo headless**: testar `--headless=new` (pode aumentar detecção de automação).
- **Múltiplas contas**: ler credenciais de um Excel ou de outro arquivo e rodar o fluxo em loop; para muitos acessos, considerar rotação de proxy (ex.: `selenium-wire`).
- **Retentativas**: envolver a execução em retry por conta ou por etapa (ex.: novo tentativa em caso de falha no reCAPTCHA ou no download).
- **Agendamento**: usar agendador do SO (Task Scheduler, cron) para rodar `enel_login.py` periodicamente.

---

## Licença e uso

Projeto para uso pessoal e educacional. Respeite os termos de uso do portal Enel e as políticas de uso do reCAPTCHA. O uso de automação pode violar os termos do site; utilize por sua conta e risco.
