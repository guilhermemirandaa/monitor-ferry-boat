import os
import time
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import requests
from datetime import datetime
from flask import Flask
import threading

# Carrega as variáveis do arquivo .env
load_dotenv()

# ==========================================
# 1. CONFIGURAÇÕES DE SEGURANÇA
# ==========================================
USUARIO_SIGOM = os.environ.get("USUARIO_SIGOM")
SENHA_SIGOM = os.environ.get("SENHA_SIGOM")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Configuração de datas e trechos específicos
# Cada busca tem sua própria lista de "horarios".
# Se "horarios" for omitido, usa HORARIOS_PADRAO.
HORARIOS_PADRAO = ["15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]

BUSCAS = [
    {
        "data": "19/11/2026",
        "trecho": "SÃO JOAQUIM (SALVADOR) / BOM DESPACHO (ITAPARICA)",
        "sentido": "Salvador ➔ Itaparica",
        "horarios": ["15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]
    },
    {
        "data": "20/11/2026",
        "trecho": "SÃO JOAQUIM (SALVADOR) / BOM DESPACHO (ITAPARICA)",
        "sentido": "Salvador ➔ Itaparica",
        "horarios": ["06:00", "07:00", "08:00", "09:00", "10:00", "11:00" , "12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]
    },
    {
        "data": "22/11/2026",
        "trecho": "BOM DESPACHO (ITAPARICA) / SÃO JOAQUIM (SALVADOR)",
        "sentido": "Itaparica ➔ Salvador",
        "horarios": ["12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00" , "21:00"]
    }
]

# ==========================================
# 2. FUNÇÕES DO BOT
# ==========================================
def enviar_notificacao(mensagem):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem}
    try:
        requests.post(url, json=payload)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Notificação enviada com sucesso no Telegram!")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Erro ao enviar notificação: {e}")

def monitorar_travessia():
    with sync_playwright() as p:
        # headless=True para rodar invisível no servidor
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Acessando o portal...")
            page.goto("https://portalsigomits.internacionaltravessias.com.br:8744/wbc-st5/ui/login.faces?uat=12", timeout=60000)

            print(f"[{datetime.now().strftime('%H:%M:%S')}] Preenchendo login...")
            page.fill('[id="frm_login_component:formLogin:username"]', USUARIO_SIGOM)
            page.fill('[id="frm_login_component:formLogin:password"]', SENHA_SIGOM)
            page.click('[id="frm_login_component:formLogin:btnConectarLogin"]')

            page.wait_for_timeout(4000)

            # --- MENU ORIGINAL ---
            page.click("text=Tickets")
            page.click("text=Hora Marcada - Veículo")
            
            page.wait_for_timeout(3000)

            # --- LOOP DE BUSCAS ---
            for busca in BUSCAS:
                data_atual = busca["data"]
                trecho_atual = busca["trecho"]
                sentido_atual = busca["sentido"]
                horarios_atual = busca.get("horarios", HORARIOS_PADRAO)

                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Preenchendo os filtros para {data_atual} ({sentido_atual})...")
                
                # Preenche a data
                page.evaluate('(data) => { document.getElementById("form:dataViagem_input").removeAttribute("readonly"); document.getElementById("form:dataViagem_input").value = data; }', data_atual)
                
                # Preenche o trecho
                page.click('[id="form:trecho"] .ui-selectonemenu-trigger')
                page.wait_for_timeout(1000)
                page.click(f'li:has-text("{trecho_atual}")')
            
                # Clica em Pesquisar
                page.click('[id="form:saveRequestButton"]')
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Aguardando o site carregar os resultados...")

                # Tempo para a tabela carregar sem pular a paginação
                page.wait_for_timeout(6000)

                alertas_vagas = []

                # --- PAGINAÇÃO ---
                for numero_pagina in range(1, 4):
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Lendo horários da página {numero_pagina}...")
                    
                    linhas = page.locator('[id="form:dataTableListaViagens"] tbody tr').all()
                    
                    for linha in linhas:
                        colunas = linha.locator('td').all()
                        if len(colunas) >= 5:
                            horario_tabela = colunas[2].inner_text().strip()
                            vagas_texto = colunas[4].inner_text().strip()
                            
                            vagas_disponiveis = int(vagas_texto) if vagas_texto.isdigit() else 0

                            if horario_tabela in horarios_atual and vagas_disponiveis > 0:
                                alertas_vagas.append(f"⏰ {horario_tabela} -> 🚗 Vagas: {vagas_disponiveis}")

                    botao_proximo = page.locator('.ui-paginator-next').first
                    classe_botao = botao_proximo.get_attribute('class') or ""
                    
                    if botao_proximo.count() > 0 and 'ui-state-disabled' not in classe_botao:
                        botao_proximo.click()
                        page.wait_for_timeout(3000)
                    else:
                        break 

                # --- ENVIO DE MENSAGEM ---
                if alertas_vagas:
                    linhas_mensagem = "\n".join(alertas_vagas)
                    mensagem = (
                        f"🚨 VAGA(S) ENCONTRADA(S) NO FERRY! 🚨\n\n"
                        f"🗓 Data: {data_atual}\n"
                        f"⛴️ Sentido: {sentido_atual}\n\n"
                        f"{linhas_mensagem}\n\n"
                        f"Corra no site para comprar!"
                    )
                    enviar_notificacao(mensagem)
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Nenhuma vaga em {data_atual} ({sentido_atual}).")

        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Erro durante a navegação: {e}")
        finally:
            browser.close()

# ==========================================
# 3. SERVIDOR FLASK (PARA O RENDER) E LOOP DO BOT
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot do Ferry Boat está rodando e monitorando vagas na nuvem!"

def iniciar_bot():
    print("Iniciando o bot de monitoramento do Ferry Boat na nuvem...")
    
    datas_monitoradas = "\n".join(
        f"• {b['data']} ({b['sentido']}): {', '.join(b.get('horarios', HORARIOS_PADRAO))}"
        for b in BUSCAS
    )
    mensagem_inicio = f"✅ *Bot Iniciado na Nuvem (Render)!*\nMonitorando vagas para:\n{datas_monitoradas}"
    enviar_notificacao(mensagem_inicio)

    while True:
        monitorar_travessia()
        
        tempo_espera = 300 
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Aguardando {tempo_espera // 60} minutos para a próxima checagem...\n")
        time.sleep(tempo_espera)

if __name__ == "__main__":
    # Inicia o bot em uma thread separada para não travar o servidor web
    thread_bot = threading.Thread(target=iniciar_bot)
    thread_bot.daemon = True
    thread_bot.start()

    # Inicia o servidor web na porta que o Render exigir
    porta = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=porta)