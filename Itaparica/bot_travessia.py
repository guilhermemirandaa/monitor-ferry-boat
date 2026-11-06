"""
Bot de monitoramento de vagas do Ferry Boat (Salvador <-> Itaparica).

Como funciona, em resumo:
  1. A cada INTERVALO_CHECAGEM segundos, abre um navegador invisível (Playwright),
     faz login no portal SIGOM da Internacional Travessias e entra em
     "Tickets > Hora Marcada - Veículo".
  2. Para cada item de BUSCAS (data + trecho), pesquisa e lê a tabela de horários.
  3. Se algum dos horários desejados tiver vaga, manda um alerta no Telegram.
  4. Entre HORA_DORMIR e HORA_ACORDAR o bot fica pausado (ninguém compra de madrugada).

Também sobe um servidor web mínimo (Flask) só para plataformas como o Render
saberem que o serviço está vivo. Rodando localmente, ele não é necessário,
mas não atrapalha.

Credenciais ficam no arquivo .env (nunca no código):
  USUARIO_SIGOM, SENHA_SIGOM, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import os
import sys
import time
import threading
import textwrap
import traceback
from datetime import datetime, timedelta, timezone

import telebot                                   # envio de mensagens no Telegram
from dotenv import load_dotenv                   # lê o arquivo .env
from flask import Flask                          # servidor web "estou vivo"
from playwright.sync_api import sync_playwright  # controla o navegador

# Terminais do Windows às vezes não aceitam emojis/acentos e o print quebra.
# Forçamos UTF-8; se não for possível, seguimos assim mesmo.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# =====================================================================
# 1. CONFIGURAÇÃO — o que normalmente você vai querer editar
# =====================================================================

# Fuso de Salvador (UTC-3, sem horário de verão). Usado em todas as horas do bot,
# assim ele funciona igual mesmo rodando num servidor em outro fuso.
FUSO_SALVADOR = timezone(timedelta(hours=-3))

# Pausa da madrugada: das HORA_DORMIR até as HORA_ACORDAR o bot não checa vagas.
HORA_DORMIR = 1   # 01:00
HORA_ACORDAR = 6  # 06:00

INTERVALO_CHECAGEM = 300  # segundos entre uma rodada de checagem e outra (5 min)
LEMBRETE_MINUTOS = 30     # se uma vaga já avisada continuar aberta, lembra de novo após esse tempo
FALHAS_PARA_AVISAR = 2    # só avisa erro no Telegram depois de N rodadas seguidas falhando
LARGURA_CELULAR = 28      # máximo de caracteres por linha nas mensagens (não quebra no celular)

# Horários usados quando uma busca não define a própria lista "horarios".
HORARIOS_PADRAO = ["15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]

# Cada item é uma pesquisa feita no site:
#   data     -> DD/MM/AAAA
#   trecho   -> texto EXATAMENTE como aparece na lista de trechos do site
#   sentido  -> texto livre, só para as mensagens (também agrupa as datas)
#   horarios -> horários (HH:MM) que interessam; os outros são ignorados
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

# Credenciais lidas do arquivo .env
load_dotenv()
USUARIO_SIGOM = os.environ.get("USUARIO_SIGOM")
SENHA_SIGOM = os.environ.get("SENHA_SIGOM")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# =====================================================================
# 2. FORMATAÇÃO DE TEXTO — logs e mensagens curtas para o celular
# =====================================================================

DIAS_SEMANA = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]  # índice = date.weekday()
ICONES_SENTIDO = ["🟦", "🟧", "🟩", "🟪"]  # um por sentido, na ordem em que aparecem em BUSCAS

def log(texto):
    """Imprime no terminal com a hora de Salvador na frente: [13:49:22] texto"""
    print(f"[{datetime.now(FUSO_SALVADOR).strftime('%H:%M:%S')}] {texto}")

def formatar_hora(hhmm):
    """'15:00' -> '15h' | '15:30' -> '15h30'. Se o formato for estranho, devolve como veio."""
    try:
        h, m = hhmm.split(":")[:2]
        return f"{h}h" if m == "00" else f"{h}h{m}"
    except ValueError:
        return hhmm

def formatar_data(data_str):
    """'19/11/2026' -> 'Qui 19/11'"""
    data = datetime.strptime(data_str, "%d/%m/%Y").date()
    return f"{DIAS_SEMANA[data.weekday()]} {data.strftime('%d/%m')}"

def formatar_horarios(horarios):
    """Resume uma lista de horários em linhas curtas.

    De hora em hora e seguidos -> uma faixa:  ['15h às 20h']
    Caso contrário              -> lista com até 4 por linha: ['08h, 12h, 18h30, 19h', '20h']
    """
    ordenados = sorted(horarios)
    minutos = [int(h[:2]) * 60 + int(h[3:]) for h in ordenados]
    seguidos = len(minutos) > 1 and all(b - a == 60 for a, b in zip(minutos, minutos[1:]))
    if seguidos:
        return [f"{formatar_hora(ordenados[0])} às {formatar_hora(ordenados[-1])}"]
    curtos = [formatar_hora(h) for h in ordenados]
    return [", ".join(curtos[i:i + 4]) for i in range(0, len(curtos), 4)]

def lista_sentidos():
    """Sentidos distintos, na ordem em que aparecem em BUSCAS."""
    sentidos = []
    for b in BUSCAS:
        if b["sentido"] not in sentidos:
            sentidos.append(b["sentido"])
    return sentidos

def icone_sentido(sentido):
    """Ícone fixo de cada sentido, para ser o mesmo em todas as mensagens."""
    return ICONES_SENTIDO[lista_sentidos().index(sentido) % len(ICONES_SENTIDO)]

def resumir_erro(erro):
    """Troca a mensagem técnica do Playwright por uma frase simples,
    já quebrada na largura do celular."""
    texto = str(erro)
    if "Timeout" in texto:
        resumo = "O site demorou demais para responder."
    elif "not visible" in texto:
        resumo = "Um botão ou menu do site não apareceu."
    elif "context was destroyed" in texto:
        resumo = "A página recarregou no meio da busca."
    elif "não atualizou" in texto:  # erro lançado por ler_vagas()
        resumo = "O site não carregou os resultados da busca."
    elif "net::" in texto or "ERR_" in texto:
        resumo = "Sem conexão com o site."
    else:
        # Erro desconhecido: só a 1ª linha, cortada, para não virar um textão
        resumo = texto.splitlines()[0][:100] if texto else type(erro).__name__
    return "\n".join(textwrap.wrap(resumo, LARGURA_CELULAR))


# =====================================================================
# 3. MENSAGENS DO TELEGRAM
# =====================================================================

def enviar_notificacao(mensagem):
    """Envia uma mensagem no Telegram. Tenta até 3 vezes se a internet falhar;
    se não conseguir, só registra no terminal (o bot continua rodando)."""
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

def montar_mensagem_inicio():
    """Mensagem enviada quando o bot liga: datas agrupadas por sentido,
    horários resumidos e configurações principais. Datas passadas são omitidas."""
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
            linhas_sentido += [f"       {f}" for f in faixas[1:]]  # linhas extras, alinhadas
        if linhas_sentido:
            linhas += ["", f"{icone_sentido(sentido)} {sentido}"] + linhas_sentido

    if len(linhas) == 1:  # só o título: nenhuma data futura
        linhas += ["", "⚠️ Nenhuma data futura configurada!"]

    linhas += [
        "",
        f"🔄 Checando a cada {INTERVALO_CHECAGEM // 60} min",
        f"😴 Pausa das {HORA_DORMIR:02d}h às {HORA_ACORDAR:02d}h",
    ]
    return "\n".join(linhas)

def mensagem_erro(erro):
    """Aviso enviado quando o site falha várias rodadas seguidas."""
    return (
        f"⚠️ Problema no bot\n\n"
        f"O site do Sigom falhou\n"
        f"ou está lento.\n\n"
        f"Motivo:\n{resumir_erro(erro)}\n\n"
        f"🔄 Sigo tentando a cada {INTERVALO_CHECAGEM // 60} min"
    )


# =====================================================================
# 4. NAVEGAÇÃO NO SITE (Playwright)
#
# O portal é feito com PrimeFaces (JSF): quase tudo é atualizado por AJAX,
# sem recarregar a página. Por isso há esperas e checagens extras — o site
# às vezes demora ou mostra resultados antigos.
# =====================================================================

def esperar_ajax(page, minimo_ms):
    """Espera um tempo mínimo e depois espera a fila de AJAX do PrimeFaces esvaziar
    (ou seja, o site terminou de atualizar a tela). Se não der para checar a fila,
    fica só com a espera mínima."""
    page.wait_for_timeout(minimo_ms)
    try:
        page.wait_for_function(
            "() => !window.PrimeFaces || !PrimeFaces.ajax || !PrimeFaces.ajax.Queue || PrimeFaces.ajax.Queue.isEmpty()",
            timeout=30000,
        )
    except Exception:
        pass

def preencher_data(page, data, tentativas=3):
    """Escreve a data no campo do calendário.

    O campo é 'readonly' (só aceita clique no calendário), então removemos esse
    atributo via JavaScript e colocamos o valor direto. Se a página recarregar
    bem nessa hora ("context was destroyed"), espera e tenta de novo."""
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
            # Outros erros (ou a última tentativa) sobem para quem chamou
            if "context was destroyed" not in str(e) or tentativa == tentativas:
                raise
            log(f"Página recarregou ao preencher a data, tentando de novo ({tentativa}/{tentativas})...")
            page.wait_for_timeout(2000)

def clicar_paginador(page, classe):
    """Clica num botão do paginador da tabela ('ui-paginator-first' ou 'ui-paginator-next').
    Devolve False se o botão não existe ou está desativado (ex.: já na última página)."""
    botao = page.locator(f'.{classe}').first
    # Confere se existe ANTES de ler o atributo: sem o botão, o Playwright
    # ficaria 30s esperando por ele e daria erro.
    if botao.count() == 0:
        return False
    if 'ui-state-disabled' in (botao.get_attribute('class') or ""):
        return False
    botao.click()
    esperar_ajax(page, 3000)
    return True

def ler_vagas(page, busca):
    """Pesquisa uma data/trecho e devolve as vagas nos horários desejados.

    Retorno: lista de (horario, vagas), ex.: [('15:00', 3), ('17:00', 1)].
    Lança RuntimeError se o site insistir em mostrar a tabela de outra busca.
    """
    horarios_desejados = busca.get("horarios", HORARIOS_PADRAO)

    # A tabela de resultados tem estas colunas:
    #   [0] $ (botão Comprar) | [1] DATA (dd/mm/aa) | [2] HORÁRIO | [3] TRECHO | [4] VAGAS DISPONÍVEIS
    # A data vem com ano de 2 dígitos, então convertemos a nossa para comparar.
    data_esperada = datetime.strptime(busca["data"], "%d/%m/%Y").strftime("%d/%m/%y")

    def pesquisar():
        """Preenche data + trecho e clica em Pesquisar."""
        preencher_data(page, busca["data"])
        page.click('[id="form:trecho"] .ui-selectonemenu-trigger')  # abre a lista de trechos
        page.wait_for_timeout(1000)
        page.click(f'li:has-text("{busca["trecho"]}")')            # escolhe o trecho
        page.click('[id="form:saveRequestButton"]')                 # botão Pesquisar
        esperar_ajax(page, 6000)
        # O site mantém a página do paginador entre buscas (se a busca anterior
        # terminou na página 2, a nova abre na página 2). Voltamos para a 1ª.
        clicar_paginador(page, "ui-paginator-first")

    def ler_pagina():
        """Lê a página atual da tabela.
        Devolve (linhas desta busca, quantidade de linhas que são de outra busca)."""
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

    # Garante que a tabela é desta busca. Às vezes o site ainda mostra a tabela
    # da pesquisa anterior: esperamos até 3x e, se não mudar, refazemos a busca 1x.
    # Isso evita avisar vagas de um dia como se fossem de outro.
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
            break  # tabela correta (ou vazia de verdade)
        log("Refazendo a busca...")
    else:
        # O 'else' do for só roda se o loop terminar sem 'break' (as 2 tentativas falharam)
        raise RuntimeError(f"A tabela não atualizou para {busca['data']}.")

    # Percorre até 3 páginas de resultados, guardando os horários com vaga
    encontrados = []
    for numero_pagina in range(1, 4):
        if numero_pagina > 1:
            validas, _ = ler_pagina()  # a página 1 já foi lida acima
        for colunas in validas:
            horario = colunas[2][:5]  # '15:00:00' -> '15:00'
            vagas = int(colunas[4]) if colunas[4].isdigit() else 0
            if horario in horarios_desejados and vagas > 0:
                encontrados.append((horario, vagas))
        if not clicar_paginador(page, "ui-paginator-next"):
            break  # não há próxima página

    return encontrados


# =====================================================================
# 5. ALERTAS DE VAGA — sem repetir a mesma mensagem a cada rodada
# =====================================================================

# Memória do que já foi avisado, para não mandar o mesmo alerta a cada 5 min.
#   chave: (data, sentido)
#   valor: {"horarios": set de horários já avisados, "quando": datetime do último aviso}
# Fica só na memória: se o bot for reiniciado, começa do zero (e avisa de novo).
ULTIMO_AVISO = {}

def avisar_vagas(busca, encontrados):
    """Decide se manda alerta para uma busca, com base no que já foi avisado.

    - Horário novo com vaga       -> "🚨 VAGA NO FERRY!"
    - Mesmas vagas há 30+ minutos -> "🔔 Ainda tem vaga!" (lembrete)
    - Mesmas vagas, aviso recente -> não manda nada
    - Vagas sumiram               -> esquece; se voltarem, avisa de novo
    """
    chave = (busca["data"], busca["sentido"])
    if not encontrados:
        ULTIMO_AVISO.pop(chave, None)
        log(f"Nenhuma vaga em {busca['data']}.")
        return

    agora = datetime.now(FUSO_SALVADOR)
    horarios = {h for h, _ in encontrados}
    anterior = ULTIMO_AVISO.get(chave)
    # "horarios <= anterior" = todos os horários atuais já tinham sido avisados
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


# =====================================================================
# 6. RODADA DE CHECAGEM — login + todas as buscas
# =====================================================================

def buscas_futuras():
    """Só as buscas de hoje em diante (datas passadas não são pesquisadas)."""
    hoje = datetime.now(FUSO_SALVADOR).date()
    return [b for b in BUSCAS if datetime.strptime(b["data"], "%d/%m/%Y").date() >= hoje]

def monitorar_travessia():
    """Faz uma rodada completa: abre o navegador, loga e checa cada busca.

    Devolve None se tudo deu certo, ou o último erro ocorrido
    (quem decide se avisa no Telegram é o iniciar_bot)."""
    buscas = buscas_futuras()
    if not buscas:
        log("Todas as datas já passaram. Nada para checar.")
        return None

    ultimo_erro = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)  # headless = navegador invisível
        context = browser.new_context()
        page = context.new_page()

        # --- Login e menu: se falhar aqui, não dá para fazer nenhuma busca ---
        try:
            log("Acessando o portal...")
            page.goto("https://portalsigomits.internacionaltravessias.com.br:8744/wbc-st5/ui/login.faces?uat=12", timeout=120000, wait_until="domcontentloaded")

            log("Preenchendo login...")
            page.fill('[id="frm_login_component:formLogin:username"]', USUARIO_SIGOM)
            page.fill('[id="frm_login_component:formLogin:password"]', SENHA_SIGOM)
            page.click('[id="frm_login_component:formLogin:btnConectarLogin"]')

            page.wait_for_timeout(4000)

            # Menu "Tickets > Hora Marcada - Veículo".
            # A página tem mais de um item com esse texto (um deles oculto),
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

        # --- Buscas: cada data tem seu próprio try, então erro numa não impede as outras ---
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


# =====================================================================
# 7. LOOP PRINCIPAL — repete as rodadas, pausa de madrugada, avisa erros
# =====================================================================

def validar_configuracao():
    """Confere o .env e a lista BUSCAS antes de começar.
    Devolve uma lista de problemas (vazia = tudo certo)."""
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

def iniciar_bot():
    """Loop infinito do bot (roda numa thread separada do servidor web).

    A cada volta:
      - de madrugada: dorme até HORA_ACORDAR;
      - senão: faz uma rodada e espera INTERVALO_CHECAGEM.
    Erros só viram aviso no Telegram após FALHAS_PARA_AVISAR rodadas seguidas
    (falhas passageiras do site são comuns), e avisa quando o site volta.
    """
    print("Iniciando o bot de monitoramento do Ferry Boat...")
    enviar_notificacao(montar_mensagem_inicio())

    falhas_seguidas = 0
    while True:
        try:
            # --- Pausa da madrugada ---
            agora = datetime.now(FUSO_SALVADOR)
            if HORA_DORMIR <= agora.hour < HORA_ACORDAR:
                acordar = agora.replace(hour=HORA_ACORDAR, minute=0, second=0, microsecond=0)
                segundos = (acordar - agora).total_seconds()
                log(f"Hora de dormir. Volto às {HORA_ACORDAR:02d}:00...")
                enviar_notificacao(f"😴 Boa noite!\n\nBot pausado até as {HORA_ACORDAR:02d}h.")
                time.sleep(segundos)
                enviar_notificacao("☀️ Bom dia!\n\nVoltei a monitorar\nas vagas.")
                continue

            # --- Rodada de checagem e controle de falhas ---
            erro = monitorar_travessia()
            if erro is None:
                if falhas_seguidas >= FALHAS_PARA_AVISAR:  # já tinha avisado o erro
                    enviar_notificacao("✅ Tudo certo!\n\nO site voltou a\nresponder.")
                falhas_seguidas = 0
            else:
                falhas_seguidas += 1
                log(f"Rodada com erro ({falhas_seguidas} seguida(s)).")
                if falhas_seguidas == FALHAS_PARA_AVISAR:  # '==' para avisar uma vez só
                    enviar_notificacao(mensagem_erro(erro))
        except Exception:
            # Qualquer erro inesperado é mostrado no terminal, mas o loop continua:
            # a thread do bot nunca pode morrer em silêncio.
            log("Erro inesperado no loop do bot:")
            traceback.print_exc()

        print()
        log(f"Aguardando {INTERVALO_CHECAGEM // 60} minutos...\n")
        time.sleep(INTERVALO_CHECAGEM)


# =====================================================================
# 8. SERVIDOR WEB + INÍCIO DO PROGRAMA
# =====================================================================

# Página simples que responde "estou vivo". Plataformas como o Render exigem
# um serviço web; localmente dá para abrir http://localhost:10000 para conferir.
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot do Ferry Boat está rodando a todo vapor!"

if __name__ == "__main__":
    # Para logo no início se a configuração estiver errada, mostrando o que corrigir
    problemas = validar_configuracao()
    if problemas:
        print("❌ Corrija antes de iniciar o bot:")
        for p in problemas:
            print(f"  - {p}")
        sys.exit(1)

    # O bot roda numa thread "daemon" (encerra junto com o programa, ex.: Ctrl+C),
    # enquanto a thread principal fica com o servidor Flask.
    thread_bot = threading.Thread(target=iniciar_bot)
    thread_bot.daemon = True
    thread_bot.start()

    porta = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=porta)
