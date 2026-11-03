import os
import time
import threading
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import telebot
from datetime import datetime
from flask import Flask

# Carrega as variáveis de ambiente
load_dotenv()

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
        "horarios": ["06:00", "07:00", "08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]
    },
    {
        "data": "22/11/2026",
        "trecho": "BOM DESPACHO (ITAPARICA) / SÃO JOAQUIM (SALVADOR)",
        "sentido": "Itaparica ➔ Salvador",
        "horarios": ["12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00"]
    }
]

def enviar_notificacao(mensagem):
    max_tentativas = 3
    for tentativa in range(1, max_tentativas + 1):
        try:
            bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
            bot.send_message(TELEGRAM_CHAT_ID, mensagem, timeout=60)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Notificação enviada com sucesso no Telegram!")
            break 
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Erro no Telegram (Tentativa {tentativa}/{max_tentativas}): {e}")
            time.sleep(5)

def monitorar_travessia():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Acessando o portal...")
            page.goto("https://portalsigomits.internacionaltravessias.com.br:8744/wbc-st5/ui/login.faces?uat=12", timeout=120000, wait_until="domcontentloaded")

            print(f"[{datetime.now().strftime('%H:%M:%S')}] Preenchendo login...")
            page.fill('[id="frm_login_component:formLogin:username"]', USUARIO_SIGOM)
            page.fill('[id="frm_login_component:formLogin:password"]', SENHA_SIGOM)
            page.click('[id="frm_login_component:formLogin:btnConectarLogin"]')

            page.wait_for_timeout(4000)
            
            # --- CORREÇÃO DO MENU APLICADA AQUI ---
            page.click("text=Tickets")
            page.wait_for_timeout(2000) # Espera 2 segundos para a animação do menu descer
            page.click("text=Hora Marcada - Veículo", force=True) # Força o clique no elemento
            
            page.wait_for_timeout(3000)

            for busca in BUSCAS:
                data_atual = busca["data"]
                trecho_atual = busca["trecho"]
                sentido_atual = busca["sentido"]
                horarios_atual = busca.get("horarios", HORARIOS_PADRAO)

                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Filtros para {data_atual} ({sentido_atual})...")
                page.evaluate('(data) => { document.getElementById("form:dataViagem_input").removeAttribute("readonly"); document.getElementById("form:dataViagem_input").value = data; }', data_atual)
                
                page.click('[id="form:trecho"] .ui-selectonemenu-trigger')
                page.wait_for_timeout(1000)
                page.click(f'li:has-text("{trecho_atual}")')
                page.click('[id="form:saveRequestButton"]')
                
                page.wait_for_timeout(6000)
                alertas_vagas = []

                for numero_pagina in range(1, 4):
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

                if alertas_vagas:
                    linhas_mensagem = "\n".join(alertas_vagas)
                    mensagem = f"🚨 VAGA(S) ENCONTRADA(S) NO FERRY! 🚨\n\n🗓 Data: {data_atual}\n⛴️ Sentido: {sentido_atual}\n\n{linhas_mensagem}\n\nCorra no site para comprar!"
                    enviar_notificacao(mensagem)
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Nenhuma vaga em {data_atual}.")

        except Exception as e:
            erro_resumo = str(e)[:150] 
            msg_erro = f"⚠️ ALERTA DO BOT ⚠️\nO site do Sigom falhou ou está lento!\n\nMotivo:\n{erro_resumo}"
            enviar_notificacao(msg_erro)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Erro: {e}")
        finally:
            context.close()
            browser.close()

# ==========================================
# 3. SERVIDOR FLASK (RENDER) E LOOP
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot do Ferry Boat está rodando a todo vapor!"

def iniciar_bot():
    print("Iniciando o bot de monitoramento do Ferry Boat na nuvem...")
    
    datas_monitoradas = "\n".join(
        f"• {b['data']} ({b['sentido']}): {', '.join(b.get('horarios', HORARIOS_PADRAO))}"
        for b in BUSCAS
    )
    mensagem_inicio = f"✅ Bot Iniciado!\nMonitorando vagas para:\n{datas_monitoradas}"
    enviar_notificacao(mensagem_inicio)

    while True:
        monitorar_travessia()
        tempo_espera = 300 
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Aguardando 5 minutos...\n")
        time.sleep(tempo_espera)

if __name__ == "__main__":
    thread_bot = threading.Thread(target=iniciar_bot)
    thread_bot.daemon = True
    thread_bot.start()

    porta = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=porta)
