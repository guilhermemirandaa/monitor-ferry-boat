# ⛴️ Ferry Boat Monitor Bot / Bot de Monitoramento - Ferry Boat Bahia

[![en](https://img.shields.io/badge/lang-en-red.svg)](#english) 
[![pt-br](https://img.shields.io/badge/lang-pt--br-green.svg)](#português)

---

## 🇺🇸 English

An automation bot developed in Python to monitor the availability of vehicle spots on the Ferry Boat crossing system (Salvador - Itaparica, Bahia). 

### 🚀 Features
- Automatically accesses the Internacional Travessias SIGOM portal.
- Logs into the system silently (Headless mode).
- Navigates to the vehicle ticket purchasing page.
- Scans the schedule table looking for available spots (greater than zero) at pre-defined times.
- Sends a real-time alert via **Telegram** as soon as a spot is found.

### 🛠️ Tech Stack
- **Python 3**
- **Playwright** (Web navigation and Web Scraping)
- **Requests** (Telegram API integration)
- **python-dotenv** (Environment variables and security management)

### ⚙️ How to run locally
1. Clone this repository.
2. Install the dependencies: `pip install -r requirements.txt`
3. Install the Playwright browser: `playwright install chromium`
4. Create a `.env` file with your credentials (SIGOM and Telegram).
5. Run the script: `python bot_travessia.py`

---

## 🇧🇷 Português

<a name="português"></a>
Este é um bot de automação desenvolvido em Python para monitorar a disponibilidade de vagas de veículos no sistema de travessia do Ferry Boat (Salvador - Itaparica). 

### 🚀 Funcionalidades
- Acessa o portal SIGOM da Internacional Travessias automaticamente.
- Faz login no sistema de forma silenciosa (Headless).
- Navega até a página de compra de passagens de veículos.
- Varre a tabela de horários buscando vagas maiores que zero nos horários pré-definidos.
- Envia um alerta em tempo real via **Telegram** assim que uma vaga é encontrada.

### 🛠️ Tecnologias Utilizadas
- **Python 3**
- **Playwright** (Navegação web e Web Scraping)
- **Requests** (Integração com a API do Telegram)
- **python-dotenv** (Gestão de variáveis de ambiente e segurança)

### ⚙️ Como executar localmente
1. Clone este repositório.
2. Instale as dependências: `pip install -r requirements.txt`
3. Instale o navegador do Playwright: `playwright install chromium`
4. Crie um arquivo `.env` com suas credenciais (SIGOM e Telegram).
5. Execute o script: `python bot_travessia.py`

---
*Developed by [Guilherme](https://github.com/guilherme12121)*
