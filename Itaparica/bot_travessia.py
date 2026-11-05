import os
import time
import threading
import textwrap
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import telebot
from datetime import datetime, timedelta, timezone
from flask import Flask

# Horário de Salvador (UTC-3, sem horário de verão)
FUSO_SALVADOR = timezone(timedelta(hours=-3))

# Período em que o bot não checa vagas (você está dormindo)
HORA_DORMIR = 1   # 01:00
HORA_ACORDAR = 6  # 06:00

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

def preencher_data(page, data, tentativas=3):
    """Preenche o campo de data esperando a página terminar de carregar.
    Se a página recarregar no meio (navegação), tenta de novo."""
    for tentativa in range(1, tentativas + 1):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=60000)
            page.wait_for_selector('[id="form:dataViagem_input"]', state="attached", timeout=60000)
            page.evaluate(
                '(data) => { const c = document.getElementById("form:dataViagem_input");'
                ' c.removeAttribute("readonly"); c.value = data; }',
                data,
            )
            return
        except Exception as e:
            if "context was destroyed" not in str(e) or tentativa == tentativas:
                raise
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Página recarregou ao preencher a data, tentando de novo ({tentativa}/{tentativas})...")
            page.wait_for_timeout(2000)

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
            
            # --- MENU: Tickets > Hora Marcada - Veículo ---
            # A página pode ter mais de um item com esse texto (um deles oculto),
            # então clicamos só no visível; se nenhum aparecer, clicamos via JavaScript.
            page.locator("text=Tickets").filter(visible=True).first.click()
            item_menu = page.locator("text=Hora Marcada - Veículo")
            try:
                item_menu.filter(visible=True).first.click(timeout=10000)
            except Exception:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Item do menu oculto, clicando via JavaScript...")
                item_menu.first.evaluate("el => (el.closest('a') || el).click()")

            page.wait_for_timeout(3000)

            for busca in BUSCAS:
                data_atual = busca["data"]
                trecho_atual = busca["trecho"]
                sentido_atual = busca["sentido"]
                horarios_atual = busca.get("horarios", HORARIOS_PADRAO)

                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Filtros para {data_atual} ({sentido_atual})...")
                preencher_data(page, data_atual)

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
                                palavra = "vaga" if vagas_disponiveis == 1 else "vagas"
                                alertas_vagas.append(f"⏰ {formatar_hora(horario_tabela)} · 🚗 {vagas_disponiveis} {palavra}")

                    botao_proximo = page.locator('.ui-paginator-next').first
                    classe_botao = botao_proximo.get_attribute('class') or ""
                    
                    if botao_proximo.count() > 0 and 'ui-state-disabled' not in classe_botao:
                        botao_proximo.click()
                        page.wait_for_timeout(3000)
                    else:
                        break 

                if alertas_vagas:
                    linhas_mensagem = "\n".join(alertas_vagas)
                    mensagem = (
                        f"🚨 VAGA NO FERRY! 🚨\n\n"
                        f"{icone_sentido(sentido_atual)} {sentido_atual}\n"
                        f"📅 {formatar_data(data_atual)}\n\n"
                        f"{linhas_mensagem}\n\n"
                        f"👉 Corra no site!"
                    )
                    enviar_notificacao(mensagem)
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Nenhuma vaga em {data_atual}.")

        except Exception as e:
            msg_erro = (
                f"⚠️ Problema no bot\n\n"
                f"O site do Sigom falhou\n"
                f"ou está lento.\n\n"
                f"Motivo:\n{resumir_erro(e)}\n\n"
                f"🔄 Tento de novo em {INTERVALO_CHECAGEM // 60} min"
            )
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

INTERVALO_CHECAGEM = 300  # segundos entre cada checagem

DIAS_SEMANA = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
ICONES_SENTIDO = ["🟦", "🟧", "🟩", "🟪"]
LARGURA_CELULAR = 28  # caracteres por linha para não quebrar no celular

def formatar_hora(hhmm):
    """'15:00' -> '15h' | '15:30' -> '15h30'"""
    try:
        h, m = hhmm.split(":")[:2]
        return f"{h}h" if m == "00" else f"{h}h{m}"
    except ValueError:
        return hhmm

def formatar_data(data_str):
    """'19/11/2026' -> 'Qui 19/11'"""
    data = datetime.strptime(data_str, "%d/%m/%Y").date()
    return f"{DIAS_SEMANA[data.weekday()]} {data.strftime('%d/%m')}"

def lista_sentidos():
    """Sentidos na ordem em que aparecem em BUSCAS."""
    sentidos = []
    for b in BUSCAS:
        if b["sentido"] not in sentidos:
            sentidos.append(b["sentido"])
    return sentidos

def icone_sentido(sentido):
    """Mesmo ícone do sentido em todas as mensagens."""
    return ICONES_SENTIDO[lista_sentidos().index(sentido) % len(ICONES_SENTIDO)]

def resumir_erro(erro):
    """Traduz erros comuns e quebra o texto na largura do celular."""
    texto = str(erro)
    if "Timeout" in texto:
        resumo = "O site demorou demais para responder."
    elif "not visible" in texto:
        resumo = "Um botão ou menu do site não apareceu."
    elif "context was destroyed" in texto:
        resumo = "A página recarregou no meio da busca."
    elif "net::" in texto or "ERR_" in texto:
        resumo = "Sem conexão com o site."
    else:
        resumo = texto.splitlines()[0][:100] if texto else type(erro).__name__
    return "\n".join(textwrap.wrap(resumo, LARGURA_CELULAR))

def formatar_horarios(horarios):
    """Faixa ('15h às 20h') se forem de hora em hora seguidos; senão, lista curta."""
    ordenados = sorted(horarios)
    minutos = [int(h[:2]) * 60 + int(h[3:]) for h in ordenados]
    seguidos = len(minutos) > 1 and all(b - a == 60 for a, b in zip(minutos, minutos[1:]))
    if seguidos:
        return [f"{formatar_hora(ordenados[0])} às {formatar_hora(ordenados[-1])}"]
    # Lista não sequencial: quebra em linhas de até 4 horários para caber no celular
    curtos = [formatar_hora(h) for h in ordenados]
    return [", ".join(curtos[i:i + 4]) for i in range(0, len(curtos), 4)]

def montar_mensagem_inicio():
    hoje = datetime.now(FUSO_SALVADOR).date()
    linhas = ["⛴️ Monitor do Ferry ligado!"]

    for sentido in lista_sentidos():
        linhas_sentido = []
        for b in BUSCAS:
            if b["sentido"] != sentido:
                continue
            data = datetime.strptime(b["data"], "%d/%m/%Y").date()
            if data < hoje:
                continue  # data já passou
            faixas = formatar_horarios(b.get("horarios", HORARIOS_PADRAO))
            linhas_sentido.append(f"  📅 {formatar_data(b['data'])} · {faixas[0]}")
            linhas_sentido += [f"       {f}" for f in faixas[1:]]
        if linhas_sentido:
            linhas += ["", f"{icone_sentido(sentido)} {sentido}"] + linhas_sentido

    if len(linhas) == 1:
        linhas += ["", "⚠️ Nenhuma data futura configurada!"]

    linhas += [
        "",
        f"🔄 Checando a cada {INTERVALO_CHECAGEM // 60} min",
        f"😴 Pausa das {HORA_DORMIR:02d}h às {HORA_ACORDAR:02d}h",
    ]
    return "\n".join(linhas)

def iniciar_bot():
    print("Iniciando o bot de monitoramento do Ferry Boat...")
    enviar_notificacao(montar_mensagem_inicio())

    while True:
        agora = datetime.now(FUSO_SALVADOR)
        if HORA_DORMIR <= agora.hour < HORA_ACORDAR:
            acordar = agora.replace(hour=HORA_ACORDAR, minute=0, second=0, microsecond=0)
            segundos = (acordar - agora).total_seconds()
            print(f"[{agora.strftime('%H:%M:%S')}] Hora de dormir. Volto às {HORA_ACORDAR:02d}:00...")
            enviar_notificacao(f"😴 Boa noite!\n\nBot pausado até as {HORA_ACORDAR:02d}h.")
            time.sleep(segundos)
            enviar_notificacao("☀️ Bom dia!\n\nVoltei a monitorar\nas vagas.")
            continue

        monitorar_travessia()
        tempo_espera = INTERVALO_CHECAGEM
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Aguardando {tempo_espera // 60} minutos...\n")
        time.sleep(tempo_espera)

if __name__ == "__main__":
    thread_bot = threading.Thread(target=iniciar_bot)
    thread_bot.daemon = True
    thread_bot.start()

    porta = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=porta)
