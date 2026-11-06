import os
import sys
import time
import threading
import textwrap
import traceback
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import telebot
from datetime import datetime, timedelta, timezone
from flask import Flask

# Evita erro ao imprimir emojis/acentos em terminais do Windows
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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
            log(f"Notificação enviada com sucesso no Telegram!")
            break 
        except Exception as e:
            log(f"Erro no Telegram (Tentativa {tentativa}/{max_tentativas}): {e}")
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
            log(f"Página recarregou ao preencher a data, tentando de novo ({tentativa}/{tentativas})...")
            page.wait_for_timeout(2000)

def log(texto):
    print(f"[{datetime.now(FUSO_SALVADOR).strftime('%H:%M:%S')}] {texto}")

def esperar_ajax(page, minimo_ms):
    """Espera o tempo mínimo e depois espera o site (PrimeFaces) terminar as requisições."""
    page.wait_for_timeout(minimo_ms)
    try:
        page.wait_for_function(
            "() => !window.PrimeFaces || !PrimeFaces.ajax || !PrimeFaces.ajax.Queue || PrimeFaces.ajax.Queue.isEmpty()",
            timeout=30000,
        )
    except Exception:
        pass  # se não der para checar, segue com a espera mínima

def clicar_paginador(page, classe):
    """Clica num botão do paginador ('ui-paginator-first', 'ui-paginator-next').
    Devolve False se o botão não existe ou está desativado."""
    botao = page.locator(f'.{classe}').first
    # Só lê o atributo se o botão existir (senão o Playwright espera 30s e dá erro)
    if botao.count() == 0:
        return False
    if 'ui-state-disabled' in (botao.get_attribute('class') or ""):
        return False
    botao.click()
    esperar_ajax(page, 3000)
    return True

def ler_vagas(page, busca):
    """Faz a busca de uma data/trecho e devolve [(horario, vagas), ...] nos horários desejados."""
    horarios_desejados = busca.get("horarios", HORARIOS_PADRAO)

    # Colunas da tabela: [$, DATA (dd/mm/aa), HORÁRIO, TRECHO, VAGAS DISPONÍVEIS]
    data_esperada = datetime.strptime(busca["data"], "%d/%m/%Y").strftime("%d/%m/%y")

    def pesquisar():
        preencher_data(page, busca["data"])
        page.click('[id="form:trecho"] .ui-selectonemenu-trigger')
        page.wait_for_timeout(1000)
        page.click(f'li:has-text("{busca["trecho"]}")')
        page.click('[id="form:saveRequestButton"]')
        esperar_ajax(page, 6000)
        # O site mantém a página do paginador entre buscas: volta para a 1ª
        clicar_paginador(page, "ui-paginator-first")

    def ler_pagina():
        """Devolve (linhas desta busca, quantidade de linhas de outra busca)."""
        validas, de_outra_busca = [], 0
        for linha in page.locator('[id="form:dataTableListaViagens"] tbody tr').all():
            colunas = [c.strip() for c in linha.locator('td').all_inner_texts()]
            if len(colunas) < 5:
                continue  # linha "Nenhum registro encontrado" etc.
            if colunas[1] != data_esperada or colunas[3] != busca["trecho"]:
                de_outra_busca += 1
                continue
            validas.append(colunas)
        return validas, de_outra_busca

    # Garante que a tabela é desta busca (o site às vezes mostra a anterior)
    for tentativa in range(1, 3):
        pesquisar()
        validas, de_outra_busca = ler_pagina()
        esperas = 0
        while de_outra_busca and not validas and esperas < 3:
            log("Tabela ainda mostra a busca anterior, esperando atualizar...")
            esperar_ajax(page, 3000)
            validas, de_outra_busca = ler_pagina()
            esperas += 1
        if validas or not de_outra_busca:
            break
        log("Refazendo a busca...")
    else:
        raise RuntimeError(f"A tabela não atualizou para {busca['data']}.")

    encontrados = []
    for numero_pagina in range(1, 4):  # até 3 páginas de resultados
        if numero_pagina > 1:
            validas, _ = ler_pagina()
        for colunas in validas:
            horario = colunas[2][:5]  # '15:00:00' -> '15:00'
            vagas = int(colunas[4]) if colunas[4].isdigit() else 0
            if horario in horarios_desejados and vagas > 0:
                encontrados.append((horario, vagas))
        if not clicar_paginador(page, "ui-paginator-next"):
            break

    return encontrados

# Últimas vagas avisadas por busca, para não repetir o mesmo alerta a cada 5 min
# chave (data, sentido) -> {"horarios": set de horários avisados, "quando": datetime}
ULTIMO_AVISO = {}
LEMBRETE_MINUTOS = 30  # se a vaga continuar, lembra de novo depois desse tempo

def avisar_vagas(busca, encontrados):
    chave = (busca["data"], busca["sentido"])
    if not encontrados:
        ULTIMO_AVISO.pop(chave, None)  # vaga sumiu: se voltar, avisa de novo
        log(f"Nenhuma vaga em {busca['data']}.")
        return

    agora = datetime.now(FUSO_SALVADOR)
    horarios = {h for h, _ in encontrados}
    anterior = ULTIMO_AVISO.get(chave)
    tem_novidade = anterior is None or not horarios <= anterior["horarios"]
    lembrete = anterior is not None and agora - anterior["quando"] >= timedelta(minutes=LEMBRETE_MINUTOS)

    if not tem_novidade and not lembrete:
        log(f"Vagas em {busca['data']} já avisadas, sem novidade.")
        return

    linhas = [
        f"⏰ {formatar_hora(h)} · 🚗 {v} {'vaga' if v == 1 else 'vagas'}"
        for h, v in sorted(encontrados)
    ]
    titulo = "🚨 VAGA NO FERRY! 🚨" if tem_novidade else "🔔 Ainda tem vaga!"
    mensagem = (
        f"{titulo}\n\n"
        f"{icone_sentido(busca['sentido'])} {busca['sentido']}\n"
        f"📅 {formatar_data(busca['data'])}\n\n"
        + "\n".join(linhas)
        + "\n\n👉 Corra no site!"
    )
    enviar_notificacao(mensagem)
    ULTIMO_AVISO[chave] = {"horarios": horarios, "quando": agora}

def buscas_futuras():
    hoje = datetime.now(FUSO_SALVADOR).date()
    return [b for b in BUSCAS if datetime.strptime(b["data"], "%d/%m/%Y").date() >= hoje]

def monitorar_travessia():
    """Faz uma rodada completa. Devolve None se deu tudo certo, ou o último erro."""
    buscas = buscas_futuras()
    if not buscas:
        log("Todas as datas já passaram. Nada para checar.")
        return None

    ultimo_erro = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            log("Acessando o portal...")
            page.goto("https://portalsigomits.internacionaltravessias.com.br:8744/wbc-st5/ui/login.faces?uat=12", timeout=120000, wait_until="domcontentloaded")

            log("Preenchendo login...")
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
                log("Item do menu oculto, clicando via JavaScript...")
                item_menu.first.evaluate("el => (el.closest('a') || el).click()")

            page.wait_for_timeout(3000)
        except Exception as e:
            log(f"Erro no login/menu: {e}")
            context.close()
            browser.close()
            return e

        # Cada data tem seu próprio try: erro numa não impede a checagem das outras
        for busca in buscas:
            print()
            log(f"Filtros para {busca['data']} ({busca['sentido']})...")
            try:
                avisar_vagas(busca, ler_vagas(page, busca))
            except Exception as e:
                log(f"Erro em {busca['data']}: {e}")
                ultimo_erro = e

        context.close()
        browser.close()
    return ultimo_erro

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
    elif "não atualizou" in texto:
        resumo = "O site não carregou os resultados da busca."
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

FALHAS_PARA_AVISAR = 2  # só avisa erro depois de N rodadas seguidas falhando

def validar_configuracao():
    """Confere .env e BUSCAS antes de começar. Devolve lista de problemas."""
    problemas = []
    for nome in ["USUARIO_SIGOM", "SENHA_SIGOM", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        if not os.environ.get(nome):
            problemas.append(f"Variável {nome} não está no .env")
    if not 0 <= HORA_DORMIR < HORA_ACORDAR <= 24:
        problemas.append("HORA_DORMIR precisa ser menor que HORA_ACORDAR")
    for i, b in enumerate(BUSCAS, 1):
        for campo in ["data", "trecho", "sentido"]:
            if not b.get(campo):
                problemas.append(f"Busca {i}: falta o campo '{campo}'")
        try:
            datetime.strptime(b.get("data", ""), "%d/%m/%Y")
        except ValueError:
            problemas.append(f"Busca {i}: data '{b.get('data')}' inválida (use DD/MM/AAAA)")
        for h in b.get("horarios", HORARIOS_PADRAO):
            try:
                datetime.strptime(h, "%H:%M")
            except ValueError:
                problemas.append(f"Busca {i}: horário '{h}' inválido (use HH:MM)")
    return problemas

def mensagem_erro(erro):
    return (
        f"⚠️ Problema no bot\n\n"
        f"O site do Sigom falhou\n"
        f"ou está lento.\n\n"
        f"Motivo:\n{resumir_erro(erro)}\n\n"
        f"🔄 Sigo tentando a cada {INTERVALO_CHECAGEM // 60} min"
    )

def iniciar_bot():
    print("Iniciando o bot de monitoramento do Ferry Boat...")
    enviar_notificacao(montar_mensagem_inicio())

    falhas_seguidas = 0
    while True:
        try:
            agora = datetime.now(FUSO_SALVADOR)
            if HORA_DORMIR <= agora.hour < HORA_ACORDAR:
                acordar = agora.replace(hour=HORA_ACORDAR, minute=0, second=0, microsecond=0)
                segundos = (acordar - agora).total_seconds()
                log(f"Hora de dormir. Volto às {HORA_ACORDAR:02d}:00...")
                enviar_notificacao(f"😴 Boa noite!\n\nBot pausado até as {HORA_ACORDAR:02d}h.")
                time.sleep(segundos)
                enviar_notificacao("☀️ Bom dia!\n\nVoltei a monitorar\nas vagas.")
                continue

            erro = monitorar_travessia()
            if erro is None:
                if falhas_seguidas >= FALHAS_PARA_AVISAR:
                    enviar_notificacao("✅ Tudo certo!\n\nO site voltou a\nresponder.")
                falhas_seguidas = 0
            else:
                falhas_seguidas += 1
                log(f"Rodada com erro ({falhas_seguidas} seguida(s)).")
                if falhas_seguidas == FALHAS_PARA_AVISAR:
                    enviar_notificacao(mensagem_erro(erro))
        except Exception:
            # Nunca deixa a thread do bot morrer em silêncio
            log("Erro inesperado no loop do bot:")
            traceback.print_exc()

        print()
        log(f"Aguardando {INTERVALO_CHECAGEM // 60} minutos...\n")
        time.sleep(INTERVALO_CHECAGEM)

if __name__ == "__main__":
    problemas = validar_configuracao()
    if problemas:
        print("❌ Corrija antes de iniciar o bot:")
        for p in problemas:
            print(f"  - {p}")
        sys.exit(1)

    thread_bot = threading.Thread(target=iniciar_bot)
    thread_bot.daemon = True
    thread_bot.start()

    porta = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=porta)
