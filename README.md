# Enel Login Automation

Automação de login no portal Enel Ceará com bypass de reCAPTCHA v2 via clique no checkbox.

---

## Estrutura do projeto

```
enel_login_automation/
├── enel_login.py          # Script principal
├── gerar_credenciais.py   # Gera o Excel de exemplo
├── credenciais.xlsx       # Suas credenciais (criar antes de rodar)
├── requirements.txt       # Dependências Python
└── README.md
```

---

## Pré-requisitos

### 1. Python 3.9+

### 2. Instalar dependências
```bash
pip install -r requirements.txt
```

### 3. Tesseract OCR
- Download: https://github.com/UB-Mannheim/tesseract/wiki
- Instalar em: `C:\Program Files\Tesseract-OCR\`

### 4. Google Chrome + ChromeDriver
- O `undetected-chromedriver` gerencia o ChromeDriver automaticamente.
- Apenas tenha o Chrome instalado normalmente.

---

## Configuração

Edite as constantes no topo do `enel_login.py`:

```python
EXCEL_FILE     = "credenciais.xlsx"
CHROME_PROFILE = r"c:\chromeprofilebot"   # pasta isolada para o bot
TESSERACT_EXE  = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

---

## Uso

### 1. Criar o arquivo de credenciais
```bash
python gerar_credenciais.py
```
Edite o `credenciais.xlsx` gerado com seus dados reais.

### 2. Executar
```bash
python enel_login.py
```

Os resultados são salvos em `resultados_login.xlsx` com colunas `email` e `status`.

---

## Como funciona o reCAPTCHA

O script usa **duas estratégias em cascata** para clicar no checkbox "Não sou um robô":

1. **Selenium** — troca para o iframe do reCAPTCHA e clica diretamente no elemento `span#recaptcha-anchor`
2. **OCR + mouse natural (fallback)** — se o Selenium falhar, captura a tela com Tesseract, localiza as palavras "I'm not a robot" por fuzzy matching e simula um clique humano com trajetória natural via `mousekey`

> O `mousekey` usa movimento bezier com variação aleatória de coordenadas e timing, o que reduz a chance de detecção.

---

## Sugestões de continuidade

- **Tratar reCAPTCHA v3** — se o site evoluir para v3, considerar serviços como 2captcha ou CapSolver
- **Headless mode** — testar `--headless=new` no Chrome (pode aumentar detecção)
- **Proxy rotation** — para múltiplas contas, rotacionar IPs com `selenium-wire`
- **Logging em arquivo** — adicionar `FileHandler` ao logger para persistir logs
- **Retry automático** — envolver o loop principal com lógica de retenativas por conta
