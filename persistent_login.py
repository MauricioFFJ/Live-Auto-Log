import os
import time
import threading
import json
import base64
import queue
import urllib.request
import urllib.error
from urllib.parse import quote
from datetime import datetime, timezone

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException,
    ElementClickInterceptedException, ElementNotInteractableException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from colorama import Fore, Style, init

# ===== CONFIGURAÇÃO =====
URL_BIGCLIENT = "https://habblive.in/bigclient/"
# A sessão é verificada menos vezes para deixar o ChromeDriver livre para o chat.
# A fila de GitHub usa requisições condicionais (ETag) para economizar cota.
CHECK_INTERVAL = max(15, int(os.getenv("SESSION_CHECK_INTERVAL", "60")))
COMMAND_POLL_INTERVAL = max(5, int(os.getenv("COMMAND_POLL_INTERVAL", "10")))
COMMANDS_PATH = "commands"
COMMAND_MAX_AGE_SECONDS = 600  # comandos com mais de 10 minutos são ignorados
COMMAND_MAX_ATTEMPTS = 5  # Legado: mantém compatibilidade com futuros comandos.
CAFOFO_MAX_ATTEMPTS = max(1, int(os.getenv("CAFOFO_MAX_ATTEMPTS", "2")))
RESPEITAR_MAX_ATTEMPTS = 1  # Nunca repetir automaticamente uma acao que consome respeitos.
RESPEITAR_SCRIPT_TIMEOUT = max(85, int(os.getenv("RESPEITAR_SCRIPT_TIMEOUT", "105")))
VISITAR_QUARTO_SCRIPT_TIMEOUT = max(30, int(os.getenv("VISITAR_QUARTO_SCRIPT_TIMEOUT", "60")))
TRATAR_CARINHOS_SCRIPT_TIMEOUT = max(120, int(os.getenv("TRATAR_CARINHOS_SCRIPT_TIMEOUT", "480")))
FALAR_MAX_ATTEMPTS = max(1, int(os.getenv("FALAR_MAX_ATTEMPTS", "1")))  # Evita várias tentativas longas em Chrome travado.
CHAT_MAX_SIMULTANEOS = max(1, int(os.getenv("CHAT_MAX_SIMULTANEOS", "5")))  # Contas independentes: sem fila global por padrão.
CHAT_HTTP_TIMEOUT = max(15, int(os.getenv("CHAT_HTTP_TIMEOUT", "25")))
COMMAND_RETRY_DELAY_SECONDS = max(1, int(os.getenv("COMMAND_RETRY_DELAY_SECONDS", "15")))
CHAT_FIELD_TIMEOUT = max(3, int(os.getenv("CHAT_FIELD_TIMEOUT", "8")))
CAFOFO_MENU_TIMEOUT = max(3, int(os.getenv("CAFOFO_MENU_TIMEOUT", "12")))
CAFOFO_HOUSE_TIMEOUT = max(3, int(os.getenv("CAFOFO_HOUSE_TIMEOUT", "15")))
# Legado: mantida para compatibilidade de configuração externa, o modo direto
# já verifica o valor durante o único execute_script.
CHAT_CONFIRMAR_TEXTO = os.getenv("CHAT_CONFIRMAR_TEXTO", "0").lower() in ("1", "true", "yes")
DEBUG_SESSAO = os.getenv("DEBUG_SESSAO", "0").strip().lower() not in ("0", "false", "no")
EXECUTAR_ACOES = False  # True = faz ações no quarto, False = só loga/reloga

# Configurações personalizadas
DONO_QUARTO = "Mist"
NOME_QUARTO = "Picnic - Encontre seu 🎁"
# ========================

init(autoreset=True)

status_contas = {}
lock = threading.Lock()
drivers_ativos = {}  # Mantido para status/compatibilidade; o monitor não usa Selenium.
filas_comandos = {}  # Uma fila independente por conta.
comandos_concluidos = {}  # IDs concluídos por conta.
ultima_atividade = {}  # Momento da última etapa concluída/iniciada por conta.
etapa_atividade = {}  # Etapa Selenium em andamento para localizar travamentos.

# Cada Selenium é exclusivo da thread de sua conta. A configuração permite
# até 5 envios simultâneos sem formar uma fila única no runner.
semaforo_falar = threading.BoundedSemaphore(CHAT_MAX_SIMULTANEOS)
falar_ativo = threading.Event()
contador_falar = 0
lock_contador_falar = threading.Lock()


def sinalizar_falar(iniciando):
    """A flag só é desligada quando TODAS as contas terminarem o FALAR."""
    global contador_falar
    with lock_contador_falar:
        contador_falar = max(0, contador_falar + (1 if iniciando else -1))
        if contador_falar > 0:
            falar_ativo.set()
        else:
            falar_ativo.clear()

GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")
GITHUB_TOKEN = os.getenv("COMMAND_GITHUB_TOKEN", "") or os.getenv("GITHUB_TOKEN", "")
GITHUB_REF_NAME = os.getenv("GITHUB_REF_NAME", "main")


def log(msg, color=Fore.WHITE):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{color}[{timestamp}] {msg}{Style.RESET_ALL}", flush=True)


def marcar_etapa(account_id, descricao, cor=Fore.CYAN, status=None, registrar=True):
    """Registra a etapa ANTES de uma chamada Selenium potencialmente bloqueante."""
    with lock:
        ultima_atividade[account_id] = time.monotonic()
        etapa_atividade[account_id] = descricao
        if status is not None:
            status_contas[account_id] = status
    if registrar:
        log(f"[Conta {account_id}] {descricao}", cor)


def painel_status(account_ids):
    while True:
        agora = time.monotonic()
        with lock:
            status_parts = []
            for account_id in account_ids:
                estado = status_contas.get(account_id, "⏳ Iniciando")
                ultima = ultima_atividade.get(account_id)
                etapa = etapa_atividade.get(account_id, "sem etapa registrada")
                # O painel não deve continuar afirmando 'Online' se a thread
                # estiver presa em uma chamada Selenium por muito tempo.
                # Um navegador ocioso/Online não está travado só porque passou
                # algum tempo sem uma operação Selenium. Alertar apenas quando
                # houver uma operação efetivamente em andamento.
                operacao_ativa = (estado in ("💬 FALAR", "🏠 CAFOFO", "🤝 RESPEITAR", "🚪 VISITAR QUARTO", "🌿 TRATAR E CARINHOS") or etapa.startswith(
                    ("Sessão: consultando", "Criando ChromeDriver", "Abrindo", "Preenchendo")))
                if ultima is not None and operacao_ativa:
                    sem_progresso = int(agora - ultima)
                    if sem_progresso >= 45:
                        estado = f"⚠️ Sem progresso há {sem_progresso}s ({etapa[:38]})"
                status_parts.append(f"[Conta {account_id}] {estado}")
            painel = " | ".join(status_parts)
        log(painel, Fore.BLUE)
        time.sleep(5)


# ---------- Helpers robustos ----------
def wait_click_css(driver, css, desc, timeout=30, use_js=False):
    wait = WebDriverWait(driver, timeout)
    elem = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, css)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
    time.sleep(0.2)
    if use_js:
        driver.execute_script("arguments[0].click();", elem)
    else:
        try:
            elem.click()
        except Exception:
            driver.execute_script("arguments[0].click();", elem)
    log(f"{desc} clicado.", Fore.GREEN)
    return elem


def wait_click_xpath(driver, xpath, desc, timeout=40, use_js=False):
    wait = WebDriverWait(driver, timeout)
    elem = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
    time.sleep(0.2)
    if use_js:
        driver.execute_script("arguments[0].click();", elem)
    else:
        try:
            elem.click()
        except Exception:
            driver.execute_script("arguments[0].click();", elem)
    log(f"{desc} clicado.", Fore.GREEN)
    return elem


def wait_type_css(driver, css, text, desc, timeout=30, clear_first=False, fire_input=True):
    wait = WebDriverWait(driver, timeout)
    elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
    time.sleep(0.2)
    elem.click()
    if clear_first:
        try:
            elem.clear()
        except Exception:
            pass
    elem.send_keys(text)
    if fire_input:
        try:
            driver.execute_script("arguments[0].dispatchEvent(new Event('input', {bubbles:true}));", elem)
            driver.execute_script("arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", elem)
        except Exception:
            pass
    log(f"{desc} digitado: '{text}'.", Fore.MAGENTA)
    return elem


def selecionar_opcao_dono(driver):
    select_css = "select.form-select.form-select-sm"
    wait = WebDriverWait(driver, 30)
    select_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, select_css)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", select_elem)
    time.sleep(0.2)
    driver.execute_script("""
        arguments[0].value = '2';
        arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
    """, select_elem)
    log("Filtro 'Dono' selecionado (via change event).", Fore.GREEN)
    return select_elem


def clicar_quarto_por_nome(driver, nome, timeout=40):
    mapa_maius = "ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÂÊÎÔÛÃÕÇ"
    mapa_minus = "abcdefghijklmnopqrstuvwxyzáéíóúâêîôûãõç"
    nome_lower = nome.lower()

    xpath_exato = (
        f"//div[@class='flex-grow-1 d-inline text-black text-truncate' and "
        f"translate(normalize-space(text()), '{mapa_maius}', '{mapa_minus}')='{nome_lower}']"
    )
    xpath_contains = (
        f"//div[@class='flex-grow-1 d-inline text-black text-truncate' and "
        f"contains(translate(normalize-space(text()), '{mapa_maius}', '{mapa_minus}'), '{nome_lower}')]"
    )

    try:
        return wait_click_xpath(driver, xpath_exato, f"Quarto '{nome}' (exato)", timeout=timeout, use_js=True)
    except Exception as e1:
        log(f"Quarto exato não encontrado: {repr(e1)}. Tentando por 'contém'...", Fore.YELLOW)
        return wait_click_xpath(driver, xpath_contains, f"Quarto contendo '{nome}'", timeout=timeout, use_js=True)


# ---------- Sequência de ações com retentativas ----------
def executar_acoes_no_quarto(driver, index):
    for tentativa in range(1, 4):
        try:
            log(f"[Conta {index}] Iniciando sequência (tentativa {tentativa}/3). Aguardando 15s...", Fore.YELLOW)
            time.sleep(15)

            wait_click_css(driver, ".cursor-pointer.navigation-item.icon.icon-rooms",
                           "[Navegador de Quartos]", timeout=30, use_js=True)
            time.sleep(4)

            wait_click_css(driver, "select.form-select.form-select-sm",
                           "[Menu de filtro]", timeout=30, use_js=True)
            time.sleep(4)
            selecionar_opcao_dono(driver)
            time.sleep(4)

            wait_type_css(driver,
                          "input.form-control.form-control-sm[placeholder='filtrar quartos por']",
                          DONO_QUARTO, "[Filtro de texto - dono]", timeout=30,
                          clear_first=True, fire_input=True)
            time.sleep(4)

            wait_click_css(driver,
                           ".d-flex.align-items-center.justify-content-center.btn.btn-primary.btn-sm",
                           "[Botão Buscar]", timeout=30, use_js=True)
            time.sleep(15)

            clicar_quarto_por_nome(driver, NOME_QUARTO, timeout=45)
            log(f"[Conta {index}] Entrando no quarto '{NOME_QUARTO}'.", Fore.GREEN)
            return

        except Exception as e:
            log(f"[Conta {index}] Falha na sequência (tentativa {tentativa}/3): {repr(e)}", Fore.RED)
            if tentativa < 3:
                time.sleep(6)
                try:
                    wait_click_css(driver, ".cursor-pointer.navigation-item.icon.icon-rooms",
                                   "[Reabrir Navegador de Quartos]", timeout=15, use_js=True)
                except Exception as e2:
                    log(f"[Conta {index}] Não conseguiu reabrir navegador: {repr(e2)}", Fore.YELLOW)
            else:
                log(f"[Conta {index}] Sequência falhou após 3 tentativas. Vai seguir monitorando.", Fore.RED)


# ---------- GitHub: recebimento e distribuição de comandos ----------
def github_api_request(path, etag=None):
    """Obtém conteúdo com cache condicional. Retorna (dados, etag, sem_mudanças)."""
    if not GITHUB_REPOSITORY or not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_REPOSITORY ou COMMAND_GITHUB_TOKEN/GITHUB_TOKEN não configurado.")

    caminho = quote(path, safe="/")
    branch = quote(GITHUB_REF_NAME, safe="")
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{caminho}?ref={branch}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Habblive-Persistent-Login",
    }
    if etag:
        headers["If-None-Match"] = etag

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            dados = json.loads(response.read().decode("utf-8"))
            return dados, response.headers.get("ETag"), False
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return None, etag, True
        if error.code == 404:
            return None, None, False
        raise


def listar_arquivos_comandos(etag=None):
    """Retorna (arquivos, etag_atual, não_modificado)."""
    resultado, etag_atual, nao_modificado = github_api_request(COMMANDS_PATH, etag)
    if nao_modificado:
        return [], etag_atual, True
    if not isinstance(resultado, list):
        return [], etag_atual, False
    arquivos = [
        item for item in resultado
        if item.get("type") == "file" and item.get("name", "").endswith(".json")
    ]
    return arquivos, etag_atual, False


def obter_comando(caminho):
    resultado, _, _ = github_api_request(caminho)
    if not resultado or "content" not in resultado:
        return None
    conteudo = base64.b64decode(resultado["content"]).decode("utf-8")
    return json.loads(conteudo)


class EnterIndeterminado(Exception):
    """A tentativa de ENTER gerou erro; a mensagem pode ou não ter sido enviada."""


def diagnosticar_chat(driver, account_id):
    """Coleta apenas sinais simples; não altera o quarto nem a aba."""
    marcar_etapa(account_id, "Diagnóstico: consultando URL e campos disponíveis...", Fore.YELLOW)
    try:
        url = driver.current_url
        campos = driver.find_elements(By.CSS_SELECTOR, "input.chat-input")
        quadros = driver.find_elements(By.TAG_NAME, "iframe")
        log(
            f"[Conta {account_id}] Diagnóstico: URL={url!r}; "
            f"inputs.chat-input={len(campos)}; iframes={len(quadros)}. "
            "Se inputs.chat-input=0, o chat pode não estar aberto, pode estar "
            "dentro de um iframe ou a interface pode ainda estar carregando.",
            Fore.YELLOW,
        )
    except Exception as erro:
        log(
            f"[Conta {account_id}] Diagnóstico não pôde consultar o navegador: {repr(erro)}",
            Fore.YELLOW,
        )


def enviar_mensagem(driver, account_id, mensagem, comando_id):
    """
    Caminho rápido para o chat:
      1. UMA chamada JS encontra/foca o input, altera o value pelo setter
         nativo e dispara 'input', para atualizar React/Vue quando aplicável.
      2. Chrome DevTools Protocol envia Enter com eventos de teclado reais.

    Não usa WebElement.click/clear/send_keys, os pontos que ficaram presos nos logs.
    Cada conta usa exclusivamente sua thread; até 5 FALAR paralelos no runner.
    """
    if not mensagem or len(mensagem) > 100:
        raise ValueError("Mensagem vazia ou maior que 100 caracteres.")

    inicio = time.monotonic()
    marcar_etapa(account_id, "FALAR: iniciando envio direto...", status="💬 FALAR")

    with semaforo_falar:
        sinalizar_falar(True)
        executor = getattr(driver, "command_executor", None)
        client_config = getattr(executor, "client_config", None)
        timeout_original = None
        timeout_alterado = False
        if client_config is not None:
            try:
                timeout_original = client_config.timeout
                client_config.timeout = CHAT_HTTP_TIMEOUT
                timeout_alterado = True
            except Exception:
                pass

        try:
            marcar_etapa(account_id, "FALAR: localizando e preenchendo chat via JavaScript...")
            # Usa setter nativo: el.value = ... isoladamente pode não notificar
            # os componentes controlados do React. 'input' também atende Vue.
            preparar_chat = r"""
                const texto = arguments[0];
                const seletor = 'input.chat-input[placeholder="Fale aqui..."]';
                const campo = document.querySelector(seletor)
                    || Array.from(document.querySelectorAll('input.chat-input'))
                       .find(e => !e.disabled && !e.readOnly);
                if (!campo) {
                    return {status: 'ausente', url: location.href};
                }
                if (campo.disabled || campo.readOnly) {
                    return {status: 'indisponivel', url: location.href};
                }
                campo.focus({preventScroll: true});
                const setValue = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value').set;
                setValue.call(campo, texto);
                try {
                    campo.dispatchEvent(new InputEvent('input', {
                        bubbles: true, composed: true, inputType: 'insertText', data: texto
                    }));
                } catch (_) {
                    campo.dispatchEvent(new Event('input', {bubbles: true}));
                }
                campo.dispatchEvent(new Event('change', {bubbles: true}));
                return {
                    status: 'preparado',
                    textoCorreto: campo.value === texto,
                    focado: document.activeElement === campo,
                    url: location.href
                };
            """
            # Não repetir a preparação se houve timeout HTTP: o resultado da
            # operação é incerto, mas nenhum Enter foi pressionado nesta tentativa.
            resposta = driver.execute_script(preparar_chat, mensagem)
            if not isinstance(resposta, dict):
                raise RuntimeError(f"Resposta inesperada do chat: {resposta!r}")
            if resposta.get('status') != 'preparado':
                raise RuntimeError(
                    f"Chat não disponível ({resposta.get('status')}); "
                    f"URL={resposta.get('url')!r}. Verifique se está dentro do quarto."
                )
            if not resposta.get('textoCorreto') or not resposta.get('focado'):
                raise RuntimeError(
                    "Chat localizado, mas o valor/foco não foi confirmado; "
                    "não vou disparar Enter sem essa confirmação."
                )
            preparado = time.monotonic()
            marcar_etapa(
                account_id,
                f"FALAR: texto preparado em {preparado-inicio:.1f}s; "
                "enviando ENTER via Chrome...",
                Fore.GREEN,
            )

            enter_args = {
                'key': 'Enter', 'code': 'Enter',
                'windowsVirtualKeyCode': 13, 'nativeVirtualKeyCode': 13,
            }
            # 'keyDown' com texto envia os eventos padrão de teclado do Chrome,
            # diferentemente de KeyboardEvent artificial (isTrusted=false).
            try:
                driver.execute_cdp_cmd('Input.dispatchKeyEvent', {
                    **enter_args, 'type': 'keyDown', 'text': '\r', 'unmodifiedText': '\r',
                })
            except Exception as erro:
                raise EnterIndeterminado(
                    f"ENTER keyDown teve resultado incerto para {comando_id}: "
                    f"{type(erro).__name__}: {str(erro)[:200]}"
                ) from erro

            apertado = time.monotonic()
            try:
                driver.execute_cdp_cmd('Input.dispatchKeyEvent', {
                    **enter_args, 'type': 'keyUp',
                })
            except Exception as erro:
                raise EnterIndeterminado(
                    f"ENTER keyDown executado, mas keyUp falhou em {comando_id}: "
                    f"{type(erro).__name__}: {str(erro)[:200]}"
                ) from erro

            final = time.monotonic()
            marcar_etapa(account_id, f"FALAR: ENTER enviado para {comando_id}.",
                         Fore.GREEN, status="✅ Online")
            log(
                f"[Conta {account_id}] FALAR: tempos → "
                f"preparar={preparado-inicio:.1f}s; "
                f"ENTER_down={apertado-preparado:.1f}s; "
                f"ENTER_up={final-apertado:.1f}s; total={final-inicio:.1f}s.",
                Fore.CYAN,
            )
        finally:
            if timeout_alterado:
                try:
                    client_config.timeout = timeout_original
                except Exception:
                    pass
            sinalizar_falar(False)


# ---------- Ação: RESPEITAR ----------
class RespeitoIndeterminado(Exception):
    """Parte do fluxo pode ter sido executada; repetir poderia consumir mais respeitos."""


def respeitar_usuario(driver, account_id, nome_usuario, comando_id):
    """Usa o FALAR funcional para :chooser e executa o fluxo no DOM do jogo.

    Fluxo:
      - :chooser no chat
      - filtro exato por nome; ausência encerra sem clicar em outra pessoa
      - Respeitar (X) até sumir, caso apareça primeiro
      - Mais respeito -> Ok -> nome -> Respeitar (X) até sumir
      - fechar chooser

    Cada conta executa em sua própria thread. Um comando de RESPEITAR não é
    repetido automaticamente, porque o navegador pode executar cliques mesmo
    quando a resposta do ChromeDriver se perde.
    """
    nome_usuario = str(nome_usuario or "").strip()
    if not nome_usuario or len(nome_usuario) > 60 or "\n" in nome_usuario or "\r" in nome_usuario:
        raise ValueError("Nome do usuário inválido (limite: 60 caracteres, sem quebras de linha).")

    inicio = time.monotonic()
    marcar_etapa(account_id, f"RESPEITAR: abrindo :chooser para {nome_usuario!r}...",
                 status="🤝 RESPEITAR")
    try:
        # Reutiliza EXATAMENTE a rotina FALAR que já funciona no Habblive.
        enviar_mensagem(driver, account_id, ":chooser", f"{comando_id}:chooser")
    except Exception as erro:
        raise RespeitoIndeterminado(
            f"RESPEITAR {comando_id}: resultado do :chooser incerto; "
            f"não repetirei automaticamente ({type(erro).__name__}: {str(erro)[:180]})."
        ) from erro

    marcar_etapa(account_id,
                 f"RESPEITAR: aguardando chooser, buscando {nome_usuario!r} e executando opções...",
                 status="🤝 RESPEITAR")
    try:
        # A interação JS tem esperas assíncronas. O padrão Selenium é 30s;
        # aumenta o timeout desta sessão para completar ambos os lotes.
        driver.set_script_timeout(RESPEITAR_SCRIPT_TIMEOUT)
        resposta = driver.execute_async_script(
            RESPEITAR_JS,
            nome_usuario,
            (RESPEITAR_SCRIPT_TIMEOUT - 10) * 1000,
        )
    except Exception as erro:
        raise RespeitoIndeterminado(
            f"RESPEITAR {comando_id}: execução DOM sem resposta confirmada "
            f"({type(erro).__name__}: {str(erro)[:220]}). "
            "Não repetirei cliques de respeito automaticamente."
        ) from erro

    if not isinstance(resposta, dict):
        raise RespeitoIndeterminado(
            f"RESPEITAR {comando_id}: retorno inesperado do navegador: {resposta!r}"
        )

    for passo in resposta.get("trilha", []):
        log(f"[Conta {account_id}] RESPEITAR: {passo}", Fore.CYAN)

    estado = resposta.get("estado")
    fase = resposta.get("fase")
    tempo = time.monotonic() - inicio
    anteriores = resposta.get("respeitosAntes", 0)
    novos = resposta.get("respeitosDepois", 0)
    if estado == "usuario_ausente":
        log(
            f"[Conta {account_id}] RESPEITAR {comando_id}: "
            f"{nome_usuario!r} não apareceu na lista do chooser; nenhuma pessoa foi respeitada. "
            f"Encerrado em {tempo:.1f}s.",
            Fore.YELLOW,
        )
        marcar_etapa(account_id, "RESPEITAR: usuário ausente; finalizado.",
                     Fore.YELLOW, status="✅ Online", registrar=False)
        return "usuario_ausente"

    if estado in ("concluido", "concluido_sem_fechar"):
        log(
            f"[Conta {account_id}] 🤝 RESPEITAR {comando_id}: "
            f"{nome_usuario!r}; cliques normais={anteriores}; "
            f"cliques após Mais respeito={novos}; "
            f"Ok confirmado por clique no DOM; tempo={tempo:.1f}s. "
            "A aceitação pelo servidor não foi verificada.",
            Fore.GREEN,
        )
        if estado == "concluido_sem_fechar":
            log(f"[Conta {account_id}] RESPEITAR: atenção, chooser não foi fechado.", Fore.YELLOW)
        marcar_etapa(account_id, "RESPEITAR: fluxo DOM concluído.",
                     Fore.GREEN, status="✅ Online", registrar=False)
        return estado

    raise RespeitoIndeterminado(
        f"RESPEITAR {comando_id}: fluxo incompleto na fase {fase!r}; "
        f"detalhe={resposta.get('erro', estado)!r}; "
        f"normais={anteriores}; novos={novos}. "
        "Não haverá repetição automática para evitar duplicação."
    )


RESPEITAR_JS = r"""
// Executado por Selenium.execute_async_script, dentro da pagina do Big Client.
// arguments[0]: nome exato; arguments[1]: limite de operacoes (ms).
const nomeSolicitado = String(arguments[0] || '').trim();
const limiteMs = Number(arguments[1]) || 70000;
const finalizarSelenium = arguments[arguments.length - 1];
const inicio = performance.now();
const trilha = [];
let fase = 'inicializacao';
let houveClique = false;
let anteriores = 0;
let novos = 0;
let maisComprado = false;
let terminou = false;

function encerrar(estado, detalhes = {}) {
    if (terminou) return;
    terminou = true;
    finalizarSelenium({
        estado,
        fase,
        nome: nomeSolicitado,
        respeitosAntes: anteriores,
        respeitosDepois: novos,
        usouMaisRespeito: maisComprado,
        houveClique,
        tempoMs: Math.round(performance.now() - inicio),
        trilha: trilha.slice(-45),
        ...detalhes
    });
}
function registrar(mensagem) {
    trilha.push(mensagem);
}
function texto(el) {
    return String(el?.textContent || '').replace(/\s+/g, ' ').trim();
}
function normalizar(valor) {
    return String(valor || '').normalize('NFC').replace(/\s+/g, ' ').trim().toLocaleLowerCase('pt-BR');
}
function visivel(el) {
    if (!el || !el.isConnected) return false;
    const estilo = getComputedStyle(el);
    if (estilo.display === 'none' || estilo.visibility === 'hidden' ||
        estilo.visibility === 'collapse' || estilo.pointerEvents === 'none') return false;
    const caixa = el.getBoundingClientRect();
    return caixa.width > 0 && caixa.height > 0;
}
function buscar(seletor) {
    return Array.from(document.querySelectorAll(seletor)).find(visivel) || null;
}
function aguardar(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
function tempoRestante() {
    return limiteMs - (performance.now() - inicio);
}
async function ate(fn, timeoutMs, intervalo = 140) {
    const fim = performance.now() + timeoutMs;
    while (true) {
        if (tempoRestante() < 1000) throw new Error('Tempo geral da automacao esgotado.');
        const resultado = fn();
        if (resultado) return resultado;
        if (performance.now() >= fim) return null;
        await aguardar(intervalo);
    }
}
function clicar(el, descricao) {
    if (!visivel(el)) throw new Error(`Elemento deixou de estar visivel: ${descricao}`);
    houveClique = true;
    el.click();
    registrar(`Clique: ${descricao}`);
}
function campoPesquisa() {
    return buscar('input.form-control.form-control-sm[placeholder="Digite para procurar..."]') ||
        buscar('input[placeholder="Digite para procurar..."]');
}
function inserirNome(campo) {
    if (!campo || campo.disabled || campo.readOnly) throw new Error('Pesquisa do chooser bloqueada.');
    campo.focus({preventScroll: true});
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(campo, nomeSolicitado);
    try {
        campo.dispatchEvent(new InputEvent('input', {bubbles: true, composed: true,
            data: nomeSolicitado, inputType: 'insertText'}));
    } catch (_) {
        campo.dispatchEvent(new Event('input', {bubbles: true}));
    }
    campo.dispatchEvent(new Event('change', {bubbles: true}));
    registrar(`Filtro preenchido: ${nomeSolicitado}`);
}
function buscarNome() {
    // Comparacao exata: nunca selecionar outro usuario parecido com o nome desejado.
    const filtro = campoPesquisa();
    const painel = filtro && (filtro.closest('.nitro-card') || filtro.closest('[class*="nitro-card"]'));
    const candidatos = Array.from(document.querySelectorAll('div.d-inline.text-black.text-truncate'))
        .filter(el => visivel(el) && normalizar(texto(el)) === normalizar(nomeSolicitado));
    if (!candidatos.length) return null;
    if (painel) return candidatos.find(el => painel.contains(el)) || null;
    return candidatos[0];
}
function encontrarRespeitar() {
    for (const el of document.querySelectorAll('div.menu-item.list-item')) {
        if (!visivel(el)) continue;
        const m = texto(el).match(/^Respeitar\s*\(\s*(\d+)\s*\)$/i);
        if (m) return {elemento: el, restante: Number(m[1])};
    }
    return null;
}
function encontrarMaisRespeito() {
    return Array.from(document.querySelectorAll('div.menu-item.list-item'))
        .find(el => visivel(el) && /^Mais respeito$/i.test(texto(el))) || null;
}
function encontrarOk() {
    return Array.from(document.querySelectorAll('div.btn.btn-primary.btn-sm.btn-green'))
        .find(el => visivel(el) && /^ok$/i.test(texto(el))) || null;
}
async function abrirOpcoes(tentativas = 4) {
    // Quando uma opcao ja estiver aberta, nao clicar no nome (poderia fecha-la).
    if (encontrarRespeitar() || encontrarMaisRespeito()) return true;
    for (let i = 0; i < tentativas; i++) {
        const nome = await ate(buscarNome, 2000);
        if (!nome) return false;
        clicar(nome, `usuario ${nomeSolicitado} (${i + 1}/${tentativas})`);
        const abriu = await ate(() => encontrarRespeitar() || encontrarMaisRespeito(), 1200);
        if (abriu) return true;
        await aguardar(170);
    }
    return false;
}
async function consumirDisponiveis(grupo, obrigatorio) {
    fase = `respeitar: consumindo ${grupo}`;
    const aberto = await abrirOpcoes();
    if (!aberto) {
        if (!obrigatorio) return 0;
        throw new Error(`Nao foi possivel abrir menu de respeito (${grupo}).`);
    }
    let cliques = 0;
    let ultimoRestante = null;
    for (let i = 0; i < 12; i++) {
        let botao = encontrarRespeitar();
        if (!botao) {
            // Pode ter fechado o menu depois do clique anterior.
            const pronto = await abrirOpcoes(3);
            botao = encontrarRespeitar();
            if (!botao) {
                if (!pronto && obrigatorio && cliques === 0) {
                    throw new Error(`Menu de respeito sumiu sem nenhum clique (${grupo}).`);
                }
                registrar(`Respeitar ausente no grupo ${grupo} apos ${cliques} clique(s)`);
                return cliques;
            }
        }
        if (botao.restante <= 0) return cliques;
        if (ultimoRestante !== null && botao.restante >= ultimoRestante) {
            // Evita clicar novamente no mesmo X se a interface nao confirmou alteracao.
            const mudou = await ate(() => {
                const atual = encontrarRespeitar();
                return !atual || atual.restante < ultimoRestante;
            }, 1700);
            if (!mudou) throw new Error(`Contador Respeitar nao diminuiu em ${grupo}: ${botao.restante}.`);
            botao = encontrarRespeitar();
            if (!botao) continue;
        }
        const antes = botao.restante;
        clicar(botao.elemento, `Respeitar (${antes}) - ${grupo}`);
        cliques += 1;
        ultimoRestante = antes;
        // Aguarda a mudanca de contador ou desaparecimento para nao enviar cliques em rajada.
        const atualizou = await ate(() => {
            const atual = encontrarRespeitar();
            return !atual || atual.restante < antes;
        }, 2300);
        if (!atualizou) {
            throw new Error(`Clique Respeitar (${antes}) sem atualizacao confirmada em ${grupo}.`);
        }
        await aguardar(160);
    }
    throw new Error(`Limite de 12 cliques atingido em ${grupo}; interrompido por seguranca.`);
}
async function esperarMaisRespeito() {
    fase = 'respeitar: localizando Mais respeito';
    const aberto = await abrirOpcoes(5);
    if (!aberto) throw new Error('Menu Mais respeito nao apareceu ao selecionar o usuario.');
    const mais = encontrarMaisRespeito();
    if (mais) return mais;
    // O ultimo Respeitar pode estar em processamento; aguarda e reabre menu.
    const sumiu = await ate(() => !encontrarRespeitar(), 2200);
    if (!sumiu) throw new Error('Ainda ha Respeitar (X); nao vou abrir Mais respeito.');
    await abrirOpcoes(3);
    const resultado = encontrarMaisRespeito();
    if (!resultado) throw new Error('Mais respeito nao apareceu depois dos respeitos normais.');
    return resultado;
}
function fecharChooser() {
    const campo = campoPesquisa();
    const cartao = campo && (campo.closest('.nitro-card') || campo.closest('[class*="nitro-card"]'));
    const botoes = Array.from(document.querySelectorAll(
        'div.d-flex.position-absolute.align-items-center.justify-content-center.end-2.nitro-card-header-close, .nitro-card-header-close'
    )).filter(visivel);
    const alvo = (cartao && botoes.find(btn => cartao.contains(btn))) || botoes[botoes.length - 1];
    if (!alvo) { registrar('Aviso: botao de fechar chooser nao encontrado'); return false; }
    clicar(alvo, 'fechar chooser');
    return true;
}

(async () => {
    try {
        if (!nomeSolicitado || nomeSolicitado.length > 60) {
            throw new Error('Nome do usuario invalido ou com mais de 60 caracteres.');
        }
        fase = 'aguardando janela chooser';
        const campo = await ate(campoPesquisa, 10000);
        if (!campo) throw new Error('Campo "Digite para procurar..." nao apareceu. :chooser pode nao ter aberto.');
        inserirNome(campo);
        fase = 'aguardando usuario na lista';
        const nome = await ate(buscarNome, 7000);
        if (!nome) {
            registrar(`Usuario ${nomeSolicitado} nao esta presente na lista`);
            fecharChooser();
            encerrar('usuario_ausente');
            return;
        }
        clicar(nome, `selecionar ${nomeSolicitado}`);
        await aguardar(220);
        fase = 'avaliando menu inicial';
        const menuInicial = await ate(() => encontrarRespeitar() || encontrarMaisRespeito(), 2400);
        if (!menuInicial) {
            const abriu = await abrirOpcoes(3);
            if (!abriu) throw new Error('Menu de usuario nao mostrou Respeitar nem Mais respeito.');
        }
        if (encontrarRespeitar()) {
            anteriores = await consumirDisponiveis('antes do Mais respeito', true);
        } else if (encontrarMaisRespeito()) {
            registrar('Mais respeito disponivel desde a primeira selecao.');
        } else {
            throw new Error('Menu inicial nao possui Respeitar nem Mais respeito.');
        }

        const mais = await esperarMaisRespeito();
        fase = 'clicando Mais respeito';
        clicar(mais, 'Mais respeito');
        fase = 'aguardando confirmacao Ok';
        const ok = await ate(encontrarOk, 5500);
        if (!ok) throw new Error('Botao verde Ok nao apareceu apos Mais respeito.');
        clicar(ok, 'Ok (confirmar Mais respeito)');
        maisComprado = true;
        registrar('Confirmacao Ok clicada');
        await aguardar(450);

        fase = 'respeitar: reabrindo usuario apos Ok';
        const nomeDepois = await ate(buscarNome, 4000);
        if (!nomeDepois) throw new Error('Nome do usuario desapareceu apos confirmar Mais respeito.');
        // O usuario pediu um novo clique no nome apos a compra.
        if (!encontrarRespeitar()) clicar(nomeDepois, `reabrir ${nomeSolicitado} apos Ok`);
        await aguardar(220);
        novos = await consumirDisponiveis('depois do Mais respeito', true);
        if (novos === 0) throw new Error('Nenhum respeito novo foi usado depois de Mais respeito.');

        fase = 'fechando chooser';
        const fechou = fecharChooser();
        if (!fechou) {
            encerrar('concluido_sem_fechar', {aviso: 'Respeitos enviados, mas botao fechar nao apareceu.'});
            return;
        }
        encerrar('concluido');
    } catch (erro) {
        encerrar('incompleto', {
            erro: String(erro?.message || erro),
            url: location.href
        });
    }
})();
"""



# ---------- Ação: VISITAR QUARTO ----------
class VisitaIndeterminada(Exception):
    """O clique pode ter sido executado, mas a resposta do navegador foi perdida."""


def visitar_quarto(driver, account_id, dados, comando_id):
    """Navega pela busca de dono e escolhe a sala mais próxima do nome pedido.

    O DOM é manipulado em UMA chamada assíncrona ao navegador para evitar
    múltiplos round-trips lentos do WebDriver. Não há repetição automática,
    pois um clique de entrada pode ter ocorrido antes de um timeout.
    """
    if not isinstance(dados, dict):
        raise ValueError("VISITAR QUARTO recebeu um payload inválido.")
    dono = str(dados.get("owner", "")).strip()
    quarto = str(dados.get("room", "")).strip()
    if (not dono or not quarto or len(dono) > 60 or len(quarto) > 100
            or any(c in dono + quarto for c in "\r\n")):
        raise ValueError("Nome do dono ou quarto inválido no comando.")

    marcar_etapa(
        account_id,
        f"VISITAR QUARTO: buscando quartos de {dono!r} com nome parecido a {quarto!r}...",
        status="🚪 VISITAR QUARTO",
    )
    inicio = time.monotonic()
    try:
        driver.set_script_timeout(VISITAR_QUARTO_SCRIPT_TIMEOUT)
        resposta = driver.execute_async_script(
            VISITAR_QUARTO_JS,
            dono,
            quarto,
            (VISITAR_QUARTO_SCRIPT_TIMEOUT - 5) * 1000,
        )
    except Exception as erro:
        # A navegação pode ter acontecido, inclusive interrompendo o próprio JS.
        raise VisitaIndeterminada(
            f"VISITAR QUARTO {comando_id}: navegador não confirmou o resultado "
            f"({type(erro).__name__}: {str(erro)[:240]}). Sem repetição automática."
        ) from erro

    if not isinstance(resposta, dict):
        raise VisitaIndeterminada(
            f"VISITAR QUARTO {comando_id}: retorno inesperado: {resposta!r}"
        )

    for passo in resposta.get("trilha", [])[-20:]:
        log(f"[Conta {account_id}] VISITAR QUARTO: {passo}", Fore.CYAN)

    estado = resposta.get("estado")
    decorrido = time.monotonic() - inicio
    if estado == "clique_quarto":
        nome = resposta.get("quartoSelecionado", "(sem nome)")
        confianca = resposta.get("pontuacao", 0)
        log(
            f"[Conta {account_id}] 🚪 VISITAR QUARTO {comando_id}: "
            f"clicou no quarto {nome!r} de {dono!r} "
            f"(correspondência={confianca:.2f}; {decorrido:.1f}s). "
            "Entrada no servidor não verificada.",
            Fore.GREEN,
        )
    elif estado == "quarto_ausente":
        log(
            f"[Conta {account_id}] ⚠️ VISITAR QUARTO {comando_id}: "
            f"nenhum quarto compatível de {dono!r} para {quarto!r}. "
            f"Candidatos: {resposta.get('candidatos', [])}. Sem clique.",
            Fore.YELLOW,
        )
    elif estado == "ambiguo":
        log(
            f"[Conta {account_id}] ⚠️ VISITAR QUARTO {comando_id}: "
            f"mais de um quarto parecido; não escolhi ao acaso. "
            f"Candidatos: {resposta.get('candidatos', [])}.",
            Fore.YELLOW,
        )
    elif estado == "erro":
        if resposta.get("clicouQuarto"):
            raise VisitaIndeterminada(
                f"VISITAR QUARTO {comando_id}: clique pode ter ocorrido; "
                f"etapa={resposta.get('fase')}; erro={resposta.get('erro')}."
            )
        raise RuntimeError(
            f"VISITAR QUARTO {comando_id}: fase={resposta.get('fase')}; "
            f"erro={resposta.get('erro')}."
        )
    else:
        raise RuntimeError(
            f"VISITAR QUARTO {comando_id}: estado DOM não reconhecido: {resposta!r}"
        )
    marcar_etapa(account_id, "VISITAR QUARTO: ação finalizada.",
                 Fore.GREEN, status="✅ Online", registrar=False)
    return estado


VISITAR_QUARTO_JS = r"""
// Uma única chamada Selenium. Os eventos de input/change preservam o estado
// de formulários controlados por React. A navegação real depende do Habblive.
const dono = String(arguments[0] || '').trim();
const quartoDesejado = String(arguments[1] || '').trim();
const limiteMs = Number(arguments[2]) || 50000;
const finalizarSelenium = arguments[arguments.length - 1];
const inicio = performance.now();
const trilha = [];
let fase = 'inicial';
let clicouQuarto = false;
let terminou = false;

function finalizar(estado, dados = {}) {
    if (terminou) return;
    terminou = true;
    finalizarSelenium({
        estado, fase, clicouQuarto,
        tempoMs: Math.round(performance.now() - inicio),
        trilha: trilha.slice(-30), ...dados
    });
}
function texto(el) { return String(el?.textContent || '').replace(/\s+/g, ' ').trim(); }
function normalizar(s) {
    return String(s || '').normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/[\u200b-\u200d\ufeff]/g, '')
        .toLocaleLowerCase('pt-BR').replace(/[^a-z0-9]+/g, ' ').trim()
        .replace(/\s+/g, ' ');
}
function visivel(el) {
    if (!el || !el.isConnected) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' ||
        s.visibility === 'collapse' || s.pointerEvents === 'none') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
}
function todos(seletor) {
    return Array.from(document.querySelectorAll(seletor)).filter(visivel);
}
function primeiro(seletor) { return todos(seletor)[0] || null; }
function dormir(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function restante() { return limiteMs - (performance.now() - inicio); }
async function esperar(fn, prazoMs, intervalo = 150) {
    const fim = performance.now() + Math.min(prazoMs, Math.max(0, restante() - 1000));
    for (;;) {
        if (restante() < 800) throw new Error('Tempo geral de visita esgotado.');
        const obtido = fn();
        if (obtido) return obtido;
        if (performance.now() >= fim) return null;
        await dormir(intervalo);
    }
}
function clicar(el, descricao) {
    if (!visivel(el)) throw new Error(`Elemento ausente/oculto: ${descricao}`);
    el.click();
    trilha.push('Clique: ' + descricao);
}
function selecionarDono() {
    return todos('select.form-select.form-select-sm').find(el =>
        Array.from(el.options || []).some(o =>
            o.value === '2' && normalizar(o.textContent) === 'dono')) || null;
}
function campoBusca() {
    return primeiro('input.form-control.form-control-sm[placeholder="filtrar quartos por"]') ||
        primeiro('input[placeholder="filtrar quartos por"]');
}
function preencherInput(input, valor) {
    if (!visivel(input) || input.disabled || input.readOnly) {
        throw new Error('Campo de pesquisa do dono indisponível.');
    }
    input.focus({preventScroll: true});
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setValue.call(input, valor);
    try {
        input.dispatchEvent(new InputEvent('input', {
            bubbles: true, composed: true, data: valor, inputType: 'insertText'
        }));
    } catch (_) { input.dispatchEvent(new Event('input', {bubbles: true})); }
    input.dispatchEvent(new Event('change', {bubbles: true}));
}
function alterarSelect(select, valor) {
    const setValue = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
    setValue.call(select, valor);
    select.dispatchEvent(new Event('input', {bubbles: true}));
    select.dispatchEvent(new Event('change', {bubbles: true}));
}
function nomesQuartos() {
    return todos('div.flex-grow-1.d-inline.text-black.text-truncate')
        .map(el => ({el, nome: texto(el)})).filter(x => x.nome.length > 0);
}
function similaridadeEdicao(a, b) {
    if (a === b) return 1;
    if (!a || !b) return 0;
    if (Math.abs(a.length - b.length) > Math.max(a.length, b.length) * 0.45) return 0;
    let ant = Array.from({length: b.length + 1}, (_,i) => i);
    for (let i = 1; i <= a.length; i++) {
        const atual = [i];
        for (let j = 1; j <= b.length; j++) {
            atual[j] = Math.min(atual[j-1] + 1, ant[j] + 1,
                ant[j-1] + (a[i-1] === b[j-1] ? 0 : 1));
        }
        ant = atual;
    }
    return 1 - ant[b.length] / Math.max(a.length, b.length);
}
function avaliar(nome) {
    const q = normalizar(quartoDesejado);
    const n = normalizar(nome);
    if (!q || !n) return 0;
    if (n === q) return 1;
    if (n.startsWith(q)) return 0.94 - 0.09 * (n.length - q.length) / n.length;
    if (n.includes(q)) return 0.87 - 0.07 * (n.length - q.length) / n.length;
    if (q.length >= 5 && q.includes(n)) return 0.76;
    if (q.length < 5) return 0;
    return 0.83 * similaridadeEdicao(n, q);
}
function classificacao() {
    const entradas = nomesQuartos();
    return entradas.map(x => ({...x, pontuacao: avaliar(x.nome)}))
        .sort((a,b) => b.pontuacao - a.pontuacao || a.nome.length - b.nome.length);
}
function decidir(ordenados) {
    const melhores = ordenados.filter(x => x.pontuacao >= 0.65);
    if (!melhores.length) return {tipo: 'ausente'};
    const primeiro = melhores[0];
    const segundo = melhores[1];
    if (segundo && primeiro.pontuacao < 0.999 &&
        (primeiro.pontuacao - segundo.pontuacao < 0.055) &&
        normalizar(primeiro.nome) !== normalizar(segundo.nome)) {
        return {tipo: 'ambiguo'};
    }
    return {tipo: 'escolhido', item: primeiro};
}

(async () => {
    try {
        if (!dono || !quartoDesejado) throw new Error('Dono/quarto vazios.');
        fase = 'abrindo navegador de quartos';
        let filtro = selecionarDono();
        let busca = campoBusca();
        // Se navegador já está aberto, não clicar no ícone: ele pode fechar a janela.
        if (!filtro || !busca) {
            const rooms = await esperar(() => primeiro('div.cursor-pointer.navigation-item.icon.icon-rooms'), 6500);
            if (!rooms) throw new Error('Ícone icon-rooms não encontrado.');
            clicar(rooms, 'navegador de quartos');
        }

        fase = 'selecionando filtro Dono';
        filtro = await esperar(selecionarDono, 9000);
        if (!filtro) throw new Error('Select do navegador com opção Dono (value=2) não apareceu.');
        alterarSelect(filtro, '2');
        await dormir(170);
        if (filtro.isConnected && filtro.value !== '2') {
            throw new Error('Filtro Dono não permaneceu selecionado.');
        }
        trilha.push('Filtro Dono (value=2) selecionado');

        fase = 'preenchendo nome do dono';
        busca = await esperar(campoBusca, 5000);
        if (!busca) throw new Error('Campo filtrar quartos por não apareceu.');
        preencherInput(busca, dono);
        await dormir(230);
        if (busca.isConnected && busca.value !== dono) {
            throw new Error('O campo não manteve o nome do dono (interface controlada).');
        }
        trilha.push('Dono pesquisado: ' + dono);

        fase = 'clicando botão de buscar';
        const antes = nomesQuartos().map(x => x.nome).join('\u241f');
        const contenedor = busca.closest('div.d-flex.w-100.gap-1') || busca.parentElement;
        const botao = contenedor?.querySelector('.btn.btn-primary.btn-sm.btn-darkblue');
        if (!botao || !visivel(botao)) {
            throw new Error('Botão de pesquisa ao lado do campo não encontrado.');
        }
        clicar(botao, 'pesquisar quartos por dono');

        fase = 'aguardando lista filtrada de quartos';
        // O botão dispara uma consulta. Não escolher linhas antigas enquanto
        // o React ainda está atualizando o resultado da pesquisa.
        await dormir(700);
        let ranking = [];
        const buscaIniciada = performance.now();
        const fim = performance.now() + Math.min(11000, Math.max(0, restante() - 1200));
        while (performance.now() < fim) {
            ranking = classificacao();
            const depois = ranking.map(x => x.nome).join('\u241f');
            const mudaramResultados = (depois !== antes);
            const decorridoBusca = performance.now() - buscaIniciada;
            const decisao = decidir(ranking);
            // Se resultados permaneceram idênticos, aguarda um pouco mais
            // para não clicar em um resultado possivelmente desatualizado.
            if (decisao.tipo !== 'ausente' && (mudaramResultados || decorridoBusca > 2200)) {
                break;
            }
            await dormir(220);
        }
        ranking = classificacao();
        const resumo = ranking.slice(0, 5).map(x => ({
            nome: x.nome, pontuacao: Number(x.pontuacao.toFixed(3))
        }));
        trilha.push('Quartos encontrados: ' + ranking.length);
        const decisao = decidir(ranking);
        if (decisao.tipo === 'ausente') {
            finalizar('quarto_ausente', {candidatos: resumo});
            return;
        }
        if (decisao.tipo === 'ambiguo') {
            finalizar('ambiguo', {candidatos: resumo});
            return;
        }

        fase = 'clicando quarto selecionado';
        const item = decisao.item;
        if (!visivel(item.el)) throw new Error('Quarto selecionado desapareceu da lista.');
        clicouQuarto = true; // Se a navegação interromper a resposta, será incerta.
        clicar(item.el, 'quarto ' + item.nome);
        finalizar('clique_quarto', {
            quartoSelecionado: item.nome,
            pontuacao: Number(item.pontuacao.toFixed(3)),
            candidatos: resumo
        });
    } catch (erro) {
        finalizar('erro', {erro: String(erro?.message || erro), url: location.href});
    }
})();
"""



# ---------- Ação: TRATAR E CARINHOS ----------
class CarinhosIndeterminado(Exception):
    """A interface pode ter executado cliques sem confirmar a resposta HTTP."""


def tratar_e_carinhos(driver, account_id, comando_id):
    """Percorre dinamicamente cada linha de mascote/planta do :chooser.

    Mantém nomes repetidos como entradas distintas, clica Tratar uma vez ou
    Acaricia (X) até o contador sumir, e fecha o chooser ao final.

    Executa JS assíncrono em uma única interação com o ChromeDriver para
    reduzir chamadas remotas; não reexecuta toda a sequência ao falhar.
    """
    inicio = time.monotonic()
    marcar_etapa(
        account_id,
        f"TRATAR E CARINHOS: abrindo :chooser ({comando_id})...",
        status="🌿 TRATAR E CARINHOS",
    )

    try:
        # Reutiliza exatamente o mecanismo de teclado funcional do FALAR.
        enviar_mensagem(driver, account_id, ":chooser", f"{comando_id}:chooser")
    except Exception as erro:
        raise CarinhosIndeterminado(
            f"TRATAR E CARINHOS {comando_id}: envio do :chooser incerto; "
            f"não será repetido ({type(erro).__name__}: {str(erro)[:180]})."
        ) from erro

    marcar_etapa(
        account_id,
        "TRATAR E CARINHOS: filtrando mascotes e processando linhas da lista...",
        status="🌿 TRATAR E CARINHOS",
    )

    # O chooser pode ter dezenas de animais. Aumentar também o limite de HTTP
    # evita que o transporte do Selenium cancele uma operação JS ainda ativa.
    executor = getattr(driver, "command_executor", None)
    client_config = getattr(executor, "client_config", None)
    timeout_anterior = None
    timeout_alterado = False
    if client_config is not None:
        try:
            timeout_anterior = client_config.timeout
            client_config.timeout = TRATAR_CARINHOS_SCRIPT_TIMEOUT + 30
            timeout_alterado = True
        except Exception:
            pass

    try:
        driver.set_script_timeout(TRATAR_CARINHOS_SCRIPT_TIMEOUT)
        resposta = driver.execute_async_script(
            TRATAR_CARINHOS_JS,
            (TRATAR_CARINHOS_SCRIPT_TIMEOUT - 15) * 1000,
        )
    except Exception as erro:
        raise CarinhosIndeterminado(
            f"TRATAR E CARINHOS {comando_id}: execução sem resposta confirmada "
            f"({type(erro).__name__}: {str(erro)[:230]}). "
            "Não vou reiniciar a lista porque alguns cliques podem já ter ocorrido."
        ) from erro
    finally:
        if timeout_alterado:
            try:
                client_config.timeout = timeout_anterior
            except Exception:
                pass

    if not isinstance(resposta, dict):
        raise CarinhosIndeterminado(
            f"TRATAR E CARINHOS {comando_id}: resposta inesperada: {resposta!r}"
        )

    for detalhe in resposta.get("anotacoes", []):
        log(f"[Conta {account_id}] CARINHOS: {detalhe}", Fore.CYAN)

    estado = resposta.get("estado", "desconhecido")
    qtd = resposta.get("total", 0)
    proc = resposta.get("processados", 0)
    tratados = resposta.get("tratados", 0)
    carinhos = resposta.get("carinhos", 0)
    erros = resposta.get("erros", 0)
    fechou = resposta.get("fechado", False)
    duracao = time.monotonic() - inicio

    log(
        f"[Conta {account_id}] TRATAR E CARINHOS {comando_id}: "
        f"estado={estado}; itens={qtd}; processados={proc}; "
        f"tratados={tratados}; carinhos={carinhos}; erros={erros}; "
        f"fechou={fechou}; tempo={duracao:.1f}s.",
        Fore.GREEN if estado in ("concluido", "sem_mascotes") else Fore.YELLOW,
    )

    if estado not in ("concluido", "parcial", "sem_mascotes"):
        raise CarinhosIndeterminado(
            f"TRATAR E CARINHOS {comando_id}: encerramento incompleto; "
            f"fase={resposta.get('fase')!r}, erro={resposta.get('erro')!r}. "
            "Ação NÃO será repetida automaticamente."
        )

    if not fechou:
        log(
            f"[Conta {account_id}] AVISO: chooser possivelmente ainda aberto; "
            "fechamento não confirmado.",
            Fore.YELLOW,
        )
    return estado


TRATAR_CARINHOS_JS = r"""
const responder = arguments[arguments.length - 1];
const limiteMs = Math.max(15000, Number(arguments[0]) || 360000);
const inicio = performance.now();
const anotacoes = [];
const estatisticas = { total: 0, processados: 0, tratados: 0, carinhos: 0, sem_acao: 0, erros: 0 };
let fase = 'inicializando';
let jaRespondeu = false;

function terminar(estado, extras = {}) {
    if (jaRespondeu) return;
    jaRespondeu = true;
    responder({ estado, fase, ...estatisticas, anotacoes: anotacoes.slice(0, 160), ...extras });
}
function registrar(mensagem) {
    if (anotacoes.length < 160) anotacoes.push(String(mensagem));
}
function texto(el) { return (el && el.textContent || '').trim().replace(/\s+/g, ' '); }
function normalizar(s) { return String(s || '').trim().toLocaleLowerCase('pt-BR'); }
function visivel(el) {
    if (!el || !el.isConnected) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    return el.getClientRects().length > 0;
}
function esperar(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function restante() { return limiteMs - (performance.now() - inicio); }
async function aguardarAte(fn, ms, intervalo = 120) {
    const fim = performance.now() + ms;
    for (;;) {
        if (restante() < 2200) throw new Error('Limite de tempo da automacao atingido.');
        const valor = fn();
        if (valor) return valor;
        if (performance.now() >= fim) return null;
        await esperar(intervalo);
    }
}
function seletorMascote() {
    return Array.from(document.querySelectorAll('select.form-select.form-select-sm'))
        .find(sel => visivel(sel) && Array.from(sel.options).some(op => op.value === 'mascote')) || null;
}
function raizChooser() {
    const select = seletorMascote();
    if (!select) return null;
    const card = select.closest('.nitro-card') || select.closest('[class*="nitro-card"]');
    // O filtro e as linhas podem estar em um unico painel; fallback para document.
    if (card && card.querySelector('tr.rounded.p-1')) return card;
    return document;
}
function linhasMascotes() {
    const raiz = raizChooser();
    if (!raiz) return [];
    const linhas = Array.from(raiz.querySelectorAll('tr.rounded.p-1'));
    return linhas.filter(tr => {
        if (!visivel(tr)) return false;
        const cols = tr.querySelectorAll('td');
        if (cols.length < 2) return false;
        const nome = tr.querySelector('td:first-child .d-inline.text-black.text-truncate');
        return !!nome && !!texto(nome) && /mascote|planta/i.test(texto(cols[1]));
    });
}
function nomeDaLinha(tr) {
    return texto(tr && tr.querySelector('td:first-child .d-inline.text-black.text-truncate'));
}
function inventario() {
    const ocorrencias = new Map();
    return linhasMascotes().map((tr, indice) => {
        const nome = nomeDaLinha(tr);
        const chave = normalizar(nome);
        const ocorrencia = ocorrencias.get(chave) || 0;
        ocorrencias.set(chave, ocorrencia + 1);
        return { nome, indice, ocorrencia, linhaOriginal: tr };
    });
}
function localizarLinha(item) {
    const linhas = linhasMascotes();
    if (item.linhaOriginal && linhas.includes(item.linhaOriginal)) return item.linhaOriginal;
    const naPosicao = linhas[item.indice];
    if (naPosicao && normalizar(nomeDaLinha(naPosicao)) === normalizar(item.nome)) return naPosicao;
    // Evita confundir itens com nomes repetidos. Só usa o mesmo ordinal de nome.
    const iguais = linhas.filter(tr => normalizar(nomeDaLinha(tr)) === normalizar(item.nome));
    return iguais[item.ocorrencia] || null;
}
function opcoes() {
    const menu = Array.from(document.querySelectorAll('.nitro-context-menu.visible'))
        .find(visivel);
    const op = Array.from((menu || document).querySelectorAll('div.menu-item.list-item'))
        .filter(visivel);
    for (const el of op) {
        const t = texto(el);
        if (/^tratar$/i.test(t)) return { tipo: 'tratar', el };
        const m = t.match(/^acarici(?:a|ar)\s*\(\s*(\d+)\s*\)$/i);
        if (m) return { tipo: 'acariciar', el, quantidade: Number(m[1]) };
    }
    return null;
}
function clicar(el, descricao) {
    if (!el || !el.isConnected) throw new Error('Elemento indisponivel: ' + descricao);
    el.click();
}
async function abrirOpcoes(item, tentativas = 2) {
    for (let i = 0; i < tentativas; i++) {
        const linha = localizarLinha(item);
        if (!linha) throw new Error('Mascote desapareceu da lista: ' + item.nome + ' #' + (item.ocorrencia + 1));
        const nome = linha.querySelector('td:first-child .d-inline.text-black.text-truncate') || linha;
        // O click no nome propaga para a tr, onde o chooser geralmente registra onClick.
        clicar(nome, 'selecionar ' + item.nome);
        await esperar(180);
        const opcao = await aguardarAte(opcoes, 850);
        if (opcao) return opcao;
    }
    return null;
}
async function consumirCarinhos(item, opcaoInicial) {
    let opcao = opcaoInicial;
    let cliques = 0;
    const limiteCliques = 15;
    while (cliques < limiteCliques) {
        if (opcao && opcao.tipo !== 'acariciar') return cliques;
        if (!opcao) {
            opcao = await abrirOpcoes(item, 2);
            if (!opcao || opcao.tipo !== 'acariciar') return cliques;
        }
        const antes = opcao.quantidade;
        if (!Number.isInteger(antes) || antes < 1 || antes > 99) {
            throw new Error('Contador Acaricia invalido: ' + antes);
        }
        clicar(opcao.el, 'Acaricia (' + antes + ')');
        cliques++;
        estatisticas.carinhos++;
        await esperar(180);
        // Verifica se o contador caiu ou se a opcao sumiu.
        let nova = opcoes();
        if (nova && nova.tipo === 'acariciar' && nova.quantidade >= antes) {
            nova = await aguardarAte(() => {
                const atual = opcoes();
                return (!atual || atual.tipo !== 'acariciar' || atual.quantidade < antes) ? { atual } : null;
            }, 1800);
            nova = nova ? nova.atual : opcoes();
        }
        if (nova && nova.tipo === 'acariciar') {
            if (nova.quantidade >= antes) {
                throw new Error('Contador nao diminuiu de ' + antes + '; nao vou repetir clique incerto.');
            }
            opcao = nova;
            continue;
        }
        // Alguns menus fecham apos cada clique; reabre para confirmar o contador.
        const reaberto = await abrirOpcoes(item, 1);
        if (!reaberto || reaberto.tipo !== 'acariciar') return cliques;
        if (reaberto.quantidade >= antes) {
            const diminuiu = await aguardarAte(() => {
                const a = opcoes();
                return a && a.tipo === 'acariciar' && a.quantidade < antes ? a : null;
            }, 1350);
            if (!diminuiu) throw new Error('Contador nao baixou apos reabrir o menu; sem repetir.');
            opcao = diminuiu;
        } else {
            opcao = reaberto;
        }
    }
    throw new Error('Limite de 15 cliques Acaricia atingido; interrompido por seguranca.');
}
function fecharChooser() {
    const select = seletorMascote();
    const card = select && (select.closest('.nitro-card') || select.closest('[class*="nitro-card"]'));
    const botoes = Array.from(document.querySelectorAll(
        'div.d-flex.position-absolute.align-items-center.justify-content-center.end-2.nitro-card-header-close, .nitro-card-header-close'
    )).filter(visivel);
    const botao = (card && botoes.find(el => card.contains(el))) || botoes[botoes.length - 1];
    if (!botao) return false;
    clicar(botao, 'fechar chooser');
    return true;
}

(async () => {
    try {
        fase = 'aguardando :chooser';
        const seletor = await aguardarAte(seletorMascote, 12000);
        if (!seletor) throw new Error('Seletor de tipos do chooser nao apareceu apos :chooser.');
        fase = 'selecionando tipo mascote';
        const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
        setter.call(seletor, 'mascote');
        seletor.dispatchEvent(new Event('input', { bubbles: true }));
        seletor.dispatchEvent(new Event('change', { bubbles: true }));
        registrar('Filtro mascote selecionado.');
        await esperar(450);
        if (seletor.value !== 'mascote') throw new Error('O filtro nao permaneceu em mascote.');

        fase = 'carregando lista';
        // A lista pode estar vazia. Aguarda ate 8s, sem misturar com outros tipos.
        await aguardarAte(() => linhasMascotes().length > 0, 8000);
        const itens = inventario();
        estatisticas.total = itens.length;
        registrar('Encontrados ' + itens.length + ' mascotes/plantas (nomes repetidos incluidos).');
        if (itens.length > 150) throw new Error('Mais de 150 itens; interrompido por seguranca.');
        if (!itens.length) {
            fase = 'fechando chooser sem itens';
            const fechado = fecharChooser();
            terminar('sem_mascotes', { fechado });
            return;
        }
        for (const item of itens) {
            if (restante() < 4000) throw new Error('Tempo insuficiente para concluir toda a lista.');
            fase = 'processando ' + item.nome + ' #' + (item.ocorrencia + 1);
            try {
                const opcao = await abrirOpcoes(item, 2);
                if (!opcao) {
                    estatisticas.sem_acao++;
                    registrar(item.nome + ' #' + (item.ocorrencia + 1) + ': sem Tratar/Acaricia.');
                } else if (opcao.tipo === 'tratar') {
                    clicar(opcao.el, 'Tratar ' + item.nome);
                    estatisticas.tratados++;
                    registrar(item.nome + ' #' + (item.ocorrencia + 1) + ': tratado.');
                    await esperar(220);
                } else {
                    const quantidade = await consumirCarinhos(item, opcao);
                    registrar(item.nome + ' #' + (item.ocorrencia + 1) + ': ' + quantidade + ' carinho(s).');
                }
                estatisticas.processados++;
            } catch (erroItem) {
                estatisticas.erros++;
                registrar(item.nome + ' #' + (item.ocorrencia + 1) + ': ERRO ' + String(erroItem?.message || erroItem));
                // Item com resultado incerto fica sem repeticao; segue para o proximo.
            }
            await esperar(120);
        }
        fase = 'fechando chooser';
        const fechado = fecharChooser();
        terminar(estatisticas.erros ? 'parcial' : 'concluido', { fechado });
    } catch (erro) {
        try { fecharChooser(); } catch (_) {}
        terminar('incompleto', { erro: String(erro?.message || erro), url: location.href });
    }
})();
"""


# ---------- Ação: IR AO CAFOFO ----------
CASA_CSS = ".cursor-pointer.navigation-item.icon.icon-house"
HABBO_CSS = ".cursor-pointer.navigation-item.icon.icon-habbo"


class CliqueIndeterminado(Exception):
    """O clique pode ter acontecido antes de um erro no ChromeDriver."""


def localizar_icone_visivel(driver, seletor):
    """Usa somente os ícones visíveis e habilitados na página atual."""
    for elemento in driver.find_elements(By.CSS_SELECTOR, seletor):
        if elemento.is_displayed() and elemento.is_enabled():
            return elemento
    return None


def clicar_icone_com_seguranca(driver, elemento, descricao):
    """Prefere clique nativo. Recorre a JS só quando houve interceptação."""
    try:
        elemento.click()
        return "Selenium"
    except (ElementClickInterceptedException, ElementNotInteractableException):
        # Nessas duas situações o Selenium informa que o clique não ocorreu.
        try:
            driver.execute_script("arguments[0].click();", elemento)
            return "JavaScript (clique nativo impedido)"
        except Exception as erro:
            raise CliqueIndeterminado(
                f"Resultado incerto ao clicar em {descricao}: {type(erro).__name__}: {erro}"
            ) from erro
    except Exception as erro:
        raise CliqueIndeterminado(
            f"Resultado incerto ao clicar em {descricao}: {type(erro).__name__}: {erro}"
        ) from erro


def ir_ao_cafofo(driver, account_id, comando_id):
    """Abre o CAFOFO via DOM JavaScript em UMA chamada ao navegador.

    Se houver casa visível, clica diretamente. Senão, clica no ícone Habbo,
    aguarda a casa ficar visível e então clica nela. O resultado informa
    o que foi realmente executado no DOM, não garante a navegação no servidor.

    Não usa WebElement.click/is_displayed/WebDriverWait: chamadas desse tipo
    ficaram bloqueadas por muito tempo nos logs do usuário.
    """
    inicio = time.monotonic()
    marcar_etapa(
        account_id,
        "CAFOFO: executando casa direta ou Habbo → casa via JavaScript...",
        status="🏠 CAFOFO",
    )

    # O timeout padrão de script assíncrono do Selenium é 30s.
    # Limitamos a espera dentro da página para não ultrapassar esse padrão.
    espera_ms = max(2000, min(CAFOFO_HOUSE_TIMEOUT * 1000, 18000))
    script = r"""
        const esperaMs = arguments[0];
        const concluir = arguments[arguments.length - 1];
        const seletorCasa = 'div.cursor-pointer.navigation-item.icon.icon-house';
        const seletorHabbo = 'div.cursor-pointer.navigation-item.icon.icon-habbo';
        const inicio = performance.now();
        let concluido = false;
        let menuClicado = false;
        let fase = 'procurando casa';

        function finalizar(estado, detalhes = {}) {
            if (concluido) return;
            concluido = true;
            concluir({
                estado,
                fase,
                menuClicado,
                decorridoMs: Math.round(performance.now() - inicio),
                ...detalhes
            });
        }

        function estaVisivel(elemento) {
            if (!elemento || !elemento.isConnected) return false;
            const estilo = getComputedStyle(elemento);
            if (estilo.display === 'none' ||
                estilo.visibility === 'hidden' ||
                estilo.visibility === 'collapse' ||
                estilo.pointerEvents === 'none') return false;
            const retangulo = elemento.getBoundingClientRect();
            return retangulo.width > 0 && retangulo.height > 0;
        }

        function acharVisivel(seletor) {
            const elementos = Array.from(document.querySelectorAll(seletor))
                .filter(estaVisivel);
            if (!elementos.length) return null;
            // Caso existam vários ícones, favorece o mais central.
            elementos.sort((a, b) => {
                const ra = a.getBoundingClientRect();
                const rb = b.getBoundingClientRect();
                const da = Math.abs(ra.left + ra.width / 2 - innerWidth / 2);
                const db = Math.abs(rb.left + rb.width / 2 - innerWidth / 2);
                return da - db;
            });
            return elementos[0];
        }

        try {
            const casaDireta = acharVisivel(seletorCasa);
            if (casaDireta) {
                fase = 'clicando casa diretamente';
                casaDireta.click();
                finalizar('casa_direta');
                return;
            }

            fase = 'procurando menu Habbo';
            const habbo = acharVisivel(seletorHabbo);
            if (!habbo) {
                finalizar('menu_ausente', {
                    motivo: 'Nem a casa nem o ícone Habbo estão visíveis neste documento.'
                });
                return;
            }

            fase = 'clicando menu Habbo';
            habbo.click();
            menuClicado = true;
            fase = 'aguardando casa após menu Habbo';

            function tentarCasa() {
                if (concluido) return;
                try {
                    const casa = acharVisivel(seletorCasa);
                    if (casa) {
                        fase = 'clicando casa após Habbo';
                        casa.click();
                        finalizar('menu_e_casa');
                        return;
                    }
                    if (performance.now() - inicio >= esperaMs) {
                        finalizar('casa_nao_apareceu', {
                            motivo: 'O menu Habbo recebeu um click, mas a casa não apareceu a tempo.'
                        });
                        return;
                    }
                    setTimeout(tentarCasa, 120);
                } catch (erro) {
                    finalizar('erro_js', {motivo: String(erro)});
                }
            }
            setTimeout(tentarCasa, 120);
        } catch (erro) {
            finalizar('erro_js', {motivo: String(erro)});
        }
    """

    try:
        resposta = driver.execute_async_script(script, espera_ms)
    except Exception as erro:
        # Uma resposta perdida pode significar que o clique já foi executado.
        # Não repetimos automaticamente para não reabrir/fechar o menu.
        raise CliqueIndeterminado(
            f"CAFOFO {comando_id}: ChromeDriver não confirmou a execução JS "
            f"({type(erro).__name__}: {str(erro)[:220]})."
        ) from erro

    if not isinstance(resposta, dict):
        raise CliqueIndeterminado(
            f"CAFOFO {comando_id}: resposta inesperada do navegador: {resposta!r}"
        )

    estado = resposta.get('estado')
    tempo = time.monotonic() - inicio
    if estado == 'casa_direta':
        log(
            f"[Conta {account_id}] 🏠 CAFOFO {comando_id}: casa clicada "
            f"diretamente via JavaScript em {tempo:.1f}s.", Fore.GREEN
        )
    elif estado == 'menu_e_casa':
        log(
            f"[Conta {account_id}] 🏠 CAFOFO {comando_id}: menu Habbo → "
            f"casa clicada via JavaScript em {tempo:.1f}s.", Fore.GREEN
        )
    elif estado == 'menu_ausente':
        raise RuntimeError(
            f"CAFOFO {comando_id}: casa e menu Habbo ausentes/ocultos. "
            "Verifique a interface do cliente."
        )
    else:
        # Se o menu foi acionado, repetir o comando pode fechá-lo novamente.
        erro = resposta.get('motivo', 'Resultado não confirmado.')
        if resposta.get('menuClicado') or estado == 'erro_js':
            raise CliqueIndeterminado(
                f"CAFOFO {comando_id}: estado={estado}; fase={resposta.get('fase')}; "
                f"detalhes={erro}. Sem repetição automática."
            )
        raise RuntimeError(f"CAFOFO {comando_id}: estado={estado}; {erro}")

    marcar_etapa(
        account_id, "CAFOFO: ação de clique concluída via JavaScript.",
        Fore.GREEN, status="✅ Online", registrar=False
    )


def processar_comando_da_fila(driver, account_id):
    """Executa uma ação da fila na thread proprietária do ChromeDriver."""
    fila = filas_comandos.get(account_id)
    if fila is None:
        return

    try:
        comando_id, acao, mensagem, criado_timestamp, tentativas, executar_apos = fila.get_nowait()
    except queue.Empty:
        return

    agora = time.time()
    if agora < executar_apos:
        fila.put((comando_id, acao, mensagem, criado_timestamp, tentativas, executar_apos))
        return

    nome_acao = {"say": "FALAR", "go_home": "CAFOFO", "respect": "RESPEITAR", "visit_room": "VISITAR QUARTO", "treat_pets": "TRATAR E CARINHOS"}.get(acao, acao)
    status_acao = {"say": "💬 FALAR", "go_home": "🏠 CAFOFO", "respect": "🤝 RESPEITAR", "visit_room": "🚪 VISITAR QUARTO", "treat_pets": "🌿 TRATAR E CARINHOS"}.get(acao, "🔄 Executando")
    if agora - criado_timestamp > COMMAND_MAX_AGE_SECONDS:
        log(f"[Conta {account_id}] {nome_acao} {comando_id} expirou.", Fore.YELLOW)
        return

    max_tentativas = {"say": FALAR_MAX_ATTEMPTS, "go_home": CAFOFO_MAX_ATTEMPTS,
                      "respect": RESPEITAR_MAX_ATTEMPTS, "visit_room": 1, "treat_pets": 1}.get(acao, 1)
    tentativa_atual = tentativas + 1
    marcar_etapa(
        account_id,
        f"{nome_acao}: iniciando comando {comando_id}, tentativa "
        f"{tentativa_atual}/{max_tentativas}.",
        status=status_acao,
    )

    try:
        if acao == "say":
            enviar_mensagem(driver, account_id, mensagem, comando_id)
        elif acao == "go_home":
            ir_ao_cafofo(driver, account_id, comando_id)
        elif acao == "respect":
            respeitar_usuario(driver, account_id, mensagem, comando_id)
        elif acao == "visit_room":
            visitar_quarto(driver, account_id, mensagem, comando_id)
        elif acao == "treat_pets":
            tratar_e_carinhos(driver, account_id, comando_id)
        else:
            raise ValueError(f"Ação desconhecida na fila: {acao!r}")

        with lock:
            comandos_concluidos.setdefault(comando_id, set()).add(account_id)

        if acao == "say":
            log(
                f"[Conta {account_id}] ✅ FALAR {comando_id}: ENTER enviado "
                "(recebimento pelo servidor não confirmado).",
                Fore.GREEN,
            )
        elif acao == "go_home":
            log(
                f"[Conta {account_id}] ✅ CAFOFO {comando_id}: clique realizado "
                "(navegação final não confirmada).",
                Fore.GREEN,
            )
        elif acao == "respect":
            log(
                f"[Conta {account_id}] ✅ RESPEITAR {comando_id}: "
                "processamento do chooser finalizado nesta conta.",
                Fore.GREEN,
            )
        elif acao == "visit_room":
            log(
                f"[Conta {account_id}] VISITAR QUARTO {comando_id}: "
                "processamento finalizado nesta conta.",
                Fore.CYAN,
            )
        elif acao == "treat_pets":
            log(
                f"[Conta {account_id}] TRATAR E CARINHOS {comando_id}: "
                "lista percorrida nesta conta (conferir resumo de erros).",
                Fore.GREEN,
            )

    except (EnterIndeterminado, CliqueIndeterminado, RespeitoIndeterminado, VisitaIndeterminada, CarinhosIndeterminado) as erro:
        # Não repetir ações que podem já ter acontecido (ENTER ou clique).
        marcar_etapa(
            account_id, f"{nome_acao}: resultado da ação indeterminado.",
            Fore.RED, status="⚠️ Ação incerta",
        )
        log(f"[Conta {account_id}] {repr(erro)}. Sem repetição automática.", Fore.RED)

    except Exception as erro:
        log(
            f"[Conta {account_id}] {nome_acao} {comando_id} falhou na tentativa "
            f"{tentativa_atual}: {type(erro).__name__}: {str(erro)[:450]!r}",
            Fore.YELLOW,
        )
        if acao == "say" and isinstance(erro, TimeoutException):
            # Diagnóstico DOM custa várias chamadas remotas; só tente se
            # a falha foi na espera pelo campo, não em um clique/ENTER.
            diagnosticar_chat(driver, account_id)
        if acao == "say" and (
            type(erro).__name__ == "ReadTimeoutError"
            or "Read timed out" in str(erro)
        ):
            log(
                f"[Conta {account_id}] ChromeDriver não respondeu dentro do "
                f"limite HTTP (configurado em {CHAT_HTTP_TIMEOUT}s). "
                "Isso normalmente indica navegador/runner sobrecarregado; "
                "repetições não corrigem a causa raiz.",
                Fore.RED,
            )

        idade = time.time() - criado_timestamp
        if tentativa_atual < max_tentativas and idade < COMMAND_MAX_AGE_SECONDS:
            proxima = time.time() + COMMAND_RETRY_DELAY_SECONDS
            fila.put((comando_id, acao, mensagem, criado_timestamp,
                      tentativa_atual, proxima))
            log(
                f"[Conta {account_id}] {nome_acao} {comando_id}: nova tentativa "
                f"daqui a {COMMAND_RETRY_DELAY_SECONDS}s ",
                Fore.YELLOW,
            )
        else:
            log(
                f"[Conta {account_id}] ❌ {nome_acao} {comando_id}: "
                "tentativas esgotadas ou comando expirado.",
                Fore.RED,
            )
        marcar_etapa(
            account_id, f"{nome_acao}: tentativa encerrada.",
            Fore.YELLOW, status="✅ Online", registrar=False,
        )


def monitorar_comandos(account_ids):
    """Busca comandos no GitHub e os distribui; nunca acessa objetos Selenium."""
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        log(
            "Leitor de comandos desativado: configure COMMAND_GITHUB_TOKEN e GITHUB_REPOSITORY.",
            Fore.YELLOW
        )
        return

    conhecidos = set()
    etag = None
    try:
        arquivos_iniciais, etag, _ = listar_arquivos_comandos()
        conhecidos = {item["name"] for item in arquivos_iniciais}
        log(
            f"Monitor de comandos iniciado para contas {account_ids} "
            f"(consulta a cada {COMMAND_POLL_INTERVAL}s; cache ETag ativo).",
            Fore.CYAN,
        )
    except Exception as erro:
        log(f"Não foi possível consultar a fila inicialmente: {repr(erro)}", Fore.YELLOW)

    pendentes = {}
    account_ids_set = set(account_ids)

    while True:
        try:
            arquivos, etag_atual, nao_modificado = listar_arquivos_comandos(etag)
            if not nao_modificado:
                etag = etag_atual

            # Lê apenas arquivos novos desde o início deste processo.
            for arquivo in arquivos:
                nome = arquivo.get("name", "")
                if nome in conhecidos:
                    continue

                caminho = arquivo.get("path")
                try:
                    comando = obter_comando(caminho)
                    if not comando:
                        continue

                    # Marca como conhecido depois de conseguir ler o JSON.
                    conhecidos.add(nome)

                    acao = str(comando.get("action", ""))
                    if acao not in ("say", "go_home", "respect", "visit_room", "treat_pets"):
                        continue

                    if acao == "say":
                        mensagem = str(comando.get("message", ""))
                    elif acao == "respect":
                        mensagem = str(comando.get("target_name", "")).strip()
                    elif acao == "visit_room":
                        mensagem = {
                            "owner": str(comando.get("room_owner", "")).strip(),
                            "room": str(comando.get("room_name", "")).strip(),
                        }
                    else:
                        mensagem = ""
                    alvos = set(int(item) for item in comando.get("targets", []))
                    alvos_locais = alvos.intersection(account_ids_set)

                    if not alvos_locais:
                        continue
                    if acao == "say" and (not mensagem or len(mensagem) > 100):
                        continue
                    if acao == "respect" and (not mensagem or len(mensagem) > 60 or "\n" in mensagem or "\r" in mensagem):
                        log(f"Comando RESPEITAR inválido ou sem nome de usuário: {nome}", Fore.YELLOW)
                        continue
                    if acao == "visit_room":
                        dono = mensagem["owner"]
                        quarto = mensagem["room"]
                        if (not dono or len(dono) > 60 or not quarto or len(quarto) > 100
                                or "\n" in dono + quarto or "\r" in dono + quarto):
                            log(f"Comando VISITAR QUARTO inválido: {nome}", Fore.YELLOW)
                            continue

                    data_criacao = comando.get("created_at")
                    if not data_criacao:
                        log(f"Comando ignorado por não possuir created_at: {nome}", Fore.YELLOW)
                        continue

                    criado_em = datetime.fromisoformat(data_criacao.replace("Z", "+00:00"))
                    if criado_em.tzinfo is None:
                        criado_em = criado_em.replace(tzinfo=timezone.utc)

                    idade = (datetime.now(timezone.utc) - criado_em).total_seconds()
                    if idade < -60:
                        log(f"Comando com data futura inválida ignorado: {nome}", Fore.YELLOW)
                        continue
                    if idade > COMMAND_MAX_AGE_SECONDS:
                        log(f"Comando antigo ignorado: {nome}.", Fore.YELLOW)
                        continue

                    comando_id = str(comando.get("id") or nome.removesuffix(".json"))
                    if "undefined" in comando_id.lower():
                        log(f"Comando com identificador inválido ignorado: {nome}", Fore.YELLOW)
                        continue

                    if comando_id in pendentes:
                        continue

                    pendentes[comando_id] = {
                        "action": acao,
                        "message": mensagem,
                        "targets": alvos_locais,
                        "dispatched": set(),
                        "created_at": criado_em,
                    }
                    with lock:
                        comandos_concluidos.setdefault(comando_id, set())

                    log(
                        f"Novo comando { {'say': 'FALAR', 'go_home': 'CAFOFO', 'respect': 'RESPEITAR', 'visit_room': 'VISITAR QUARTO', 'treat_pets': 'TRATAR E CARINHOS'}[acao] } recebido "
                        f"({comando_id}) para contas {sorted(alvos_locais)}.",
                        Fore.CYAN
                    )

                except Exception as erro:
                    log(f"Falha ao ler o comando {nome}: {repr(erro)}", Fore.YELLOW)

            agora = datetime.now(timezone.utc)

            # Distribui os comandos para as filas de cada conta.
            for comando_id, item in list(pendentes.items()):
                idade = (agora - item["created_at"]).total_seconds()
                if idade > COMMAND_MAX_AGE_SECONDS:
                    log(
                        f"Comando {comando_id} expirou antes de todas as contas executarem.",
                        Fore.YELLOW
                    )
                    pendentes.pop(comando_id, None)
                    continue

                with lock:
                    concluidas = set(comandos_concluidos.get(comando_id, set()))

                for account_id in sorted(item["targets"]):
                    if account_id in concluidas or account_id in item["dispatched"]:
                        continue

                    fila = filas_comandos.get(account_id)
                    if fila is None:
                        continue

                    fila.put((
                        comando_id,
                        item["action"],
                        item["message"],
                        item["created_at"].timestamp(),
                        0,
                        0.0,  # disponível para execução imediata
                    ))
                    item["dispatched"].add(account_id)
                    log(
                        f"[Conta {account_id}] Comando {comando_id} adicionado à fila de execução.",
                        Fore.CYAN
                    )

                with lock:
                    concluidas = set(comandos_concluidos.get(comando_id, set()))

                if item["targets"].issubset(concluidas):
                    log(
                        f"Comando {comando_id} concluído para todas as contas selecionadas.",
                        Fore.GREEN
                    )
                    pendentes.pop(comando_id, None)

        except urllib.error.HTTPError as erro:
            if erro.code in (403, 429):
                retry_after = erro.headers.get("Retry-After", "")
                try:
                    pausa = max(60, int(retry_after))
                except (ValueError, TypeError):
                    pausa = 60
                log(
                    f"GitHub retornou HTTP {erro.code}; pausando consulta "
                    f"por {pausa}s para respeitar limite de API.",
                    Fore.YELLOW,
                )
                time.sleep(pausa)
            else:
                log(f"Erro HTTP no monitor de comandos: {repr(erro)}", Fore.RED)
        except Exception as erro:
            log(f"Erro no monitor de comandos: {repr(erro)}", Fore.RED)

        time.sleep(COMMAND_POLL_INTERVAL)


# ---------- Sessão individual de cada conta ----------
def verificar_sessao(driver, index, cliente_ausente_desde):
    """
    Uma chamada ao ChromeDriver consulta URL e estado da UI em conjunto.
    Retorna (continuar_sessao, quando_a_UI_sumiu).
    """
    marcar_etapa(index, "Sessão: consultando URL e interface...", registrar=DEBUG_SESSAO)
    estado = driver.execute_script("""
        return {
            url: window.location.href,
            quartos: !!document.querySelector('.cursor-pointer.navigation-item.icon.icon-rooms'),
            chat: !!document.querySelector('input.chat-input')
        };
    """)
    url_atual = str(estado.get("url") or "")
    tem_interface = bool(estado.get("quartos") or estado.get("chat"))

    if not tem_interface:
        if url_atual.rstrip("/") != URL_BIGCLIENT.rstrip("/"):
            log(f"[Conta {index}] ⚠️ Saiu do Big Client ({url_atual}). Relogando...", Fore.YELLOW)
            return False, cliente_ausente_desde

        if cliente_ausente_desde is None:
            cliente_ausente_desde = time.monotonic()
            log(
                f"[Conta {index}] ⚠️ Interface principal não localizada; "
                "os comandos continuarão com prioridade.",
                Fore.YELLOW,
            )
        tempo_ausente = time.monotonic() - cliente_ausente_desde
        marcar_etapa(index, "Sessão: interface temporariamente ausente.",
                     Fore.YELLOW, status="🔄 Cliente carregando", registrar=False)
        if tempo_ausente >= 90:
            log(
                f"[Conta {index}] ❌ Interface indisponível por {tempo_ausente:.0f}s. Relogando...",
                Fore.RED,
            )
            return False, cliente_ausente_desde
        return True, cliente_ausente_desde

    if cliente_ausente_desde is not None:
        log(f"[Conta {index}] ✅ Interface do cliente recarregada.", Fore.GREEN)
        if EXECUTAR_ACOES:
            executar_acoes_no_quarto(driver, index)

    marcar_etapa(index, "Sessão: verificação concluída.", Fore.GREEN,
                 status="✅ Online", registrar=DEBUG_SESSAO)
    return True, None


def iniciar_sessao(username, password, index):
    # Escalona 5 contas por grupo; evita que contas 46–50 aguardem mais de
    # dois minutos só por usarem IDs absolutos altos.
    time.sleep(((index - 1) % 5) * 3)

    while True:
        driver = None
        marcar_etapa(index, "Iniciando uma nova sessão de login.",
                     Fore.CYAN, status="🔄 Relogando")

        try:
            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--incognito")
            options.add_argument("--window-size=1366,768")

            service = Service(ChromeDriverManager().install())
            marcar_etapa(index, "Criando ChromeDriver...", Fore.CYAN)
            driver = webdriver.Chrome(service=service, options=options)

            log(f"[Conta {index}] Iniciando login para {username}...", Fore.CYAN)
            marcar_etapa(index, "Abrindo página de login...")
            driver.get("https://habblive.in/")
            wait = WebDriverWait(driver, 25)

            try:
                cookie_banner = wait.until(
                    EC.presence_of_element_located((By.ID, "cookie-law-container"))
                )
                try:
                    accept_btn = cookie_banner.find_element(By.TAG_NAME, "button")
                    driver.execute_script("arguments[0].click();", accept_btn)
                    log(f"[Conta {index}] Banner de cookies fechado.", Fore.MAGENTA)
                except Exception:
                    driver.execute_script("""
                        const el = document.getElementById('cookie-law-container');
                        if (el) el.remove();
                    """)
                    log(f"[Conta {index}] Banner de cookies removido via script.", Fore.MAGENTA)
            except TimeoutException:
                # Um banner ausente não é falha de login.
                pass

            marcar_etapa(index, "Preenchendo login...")
            wait.until(EC.presence_of_element_located((By.NAME, "username"))).send_keys(username)
            wait.until(EC.presence_of_element_located((By.NAME, "password"))).send_keys(password)
            btn_login = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".btn.big.green.login-button"))
            )
            driver.execute_script("arguments[0].click();", btn_login)

            time.sleep(5)
            marcar_etapa(index, "Abrindo o Big Client...")
            driver.get(URL_BIGCLIENT)
            log(f"[Conta {index}] ✅ Página do Big Client aberta.", Fore.GREEN)
            marcar_etapa(index, "Sessão: Big Client carregado.", Fore.GREEN,
                         status="✅ Online", registrar=False)

            with lock:
                status_contas[index] = "✅ Online"
                drivers_ativos[index] = driver
                ultima_atividade[index] = time.monotonic()

            if EXECUTAR_ACOES:
                executar_acoes_no_quarto(driver, index)
            else:
                log(
                    f"[Conta {index}] EXECUTAR_ACOES=False: o script NÃO entra "
                    "automaticamente em nenhum quarto. O FALAR exige que "
                    "o chat esteja disponível nesta sessão.",
                    Fore.YELLOW,
                )

            # Um ChromeDriver só é acessado por esta thread.
            # Verificações de sessão usam uma única consulta a cada CHECK_INTERVAL;
            # enquanto isso, as filas de comandos são consultadas a cada segundo.
            proxima_verificacao = time.monotonic() + CHECK_INTERVAL
            cliente_ausente_desde = None

            while True:
                # PRIORIDADE: registra a tentativa ANTES de qualquer checagem periódica.
                processar_comando_da_fila(driver, index)

                # Não iniciar verificação de sessão com ações esperando na fila.
                fila_pessoal = filas_comandos.get(index)
                if (time.monotonic() >= proxima_verificacao
                        and (fila_pessoal is None or fila_pessoal.empty())
                        and not falar_ativo.is_set()):
                    continuar, cliente_ausente_desde = verificar_sessao(
                        driver, index, cliente_ausente_desde,
                    )
                    proxima_verificacao = time.monotonic() + CHECK_INTERVAL
                    if not continuar:
                        marcar_etapa(index, "Reiniciando sessão após perda do cliente.",
                                     Fore.YELLOW, status="🔄 Relogando")
                        break

                time.sleep(1)

        except Exception as erro:
            log(f"[Conta {index}] ❌ Erro na sessão: {repr(erro)}", Fore.RED)
            marcar_etapa(index, "Sessão encerrada por erro; relogin necessário.",
                         Fore.RED, status="❌ Erro", registrar=False)
            time.sleep(5)

        finally:
            with lock:
                if drivers_ativos.get(index) is driver:
                    drivers_ativos.pop(index, None)
            if driver is not None:
                try:
                    log(f"[Conta {index}] Encerrando o ChromeDriver antigo.", Fore.YELLOW)
                    driver.quit()
                except Exception as erro:
                    log(f"[Conta {index}] Falha ao fechar navegador: {repr(erro)}", Fore.YELLOW)

            # A fila da conta NÃO é apagada no relogin. Mensagens não expiradas
            # continuam pendentes e serão lidas ao abrir o novo navegador.
            time.sleep(2)


# ---------- Inicialização ----------
# Lê as contas absolutas definidas pelo workflow (ex.: 21,22,23,24,25).
# Se ACCOUNT_IDS não for informado, mantém compatibilidade com a leitura antiga.
account_ids_env = os.getenv("ACCOUNT_IDS", "").strip()
if account_ids_env:
    account_ids = [int(item.strip()) for item in account_ids_env.split(",") if item.strip()]
else:
    account_ids = list(range(1, 101))

accounts = []
for account_id in account_ids:
    user = os.getenv(f"HABBLIVE_USERNAME_{account_id}")
    pwd = os.getenv(f"HABBLIVE_PASSWORD_{account_id}")
    if user and pwd:
        accounts.append((account_id, user, pwd))

if not accounts:
    raise ValueError("Nenhuma conta configurada nos secrets para os ACCOUNT_IDS informados.")

account_ids_ativos = [item[0] for item in accounts]

# Cria uma fila independente para cada conta ativa.
for account_id in account_ids_ativos:
    filas_comandos[account_id] = queue.Queue()
    ultima_atividade[account_id] = time.monotonic()
    etapa_atividade[account_id] = "Inicializando"

with lock:
    for account_id in account_ids_ativos:
        status_contas[account_id] = "⏳ Iniciando"

# Thread do painel.
painel_thread = threading.Thread(
    target=painel_status,
    args=(account_ids_ativos,),
    daemon=True
)
painel_thread.start()

# Thread que consulta o GitHub e distribui comandos, sem tocar nos navegadores.
command_thread = threading.Thread(
    target=monitorar_comandos,
    args=(account_ids_ativos,),
    daemon=True
)
command_thread.start()

# Threads individuais das contas.
threads = []
for account_id, username, password in accounts:
    thread = threading.Thread(
        target=iniciar_sessao,
        args=(username, password, account_id),
        name=f"Conta-{account_id}"
    )
    thread.start()
    threads.append(thread)

# Mantém todas as threads vivas.
for thread in threads:
    thread.join()

painel_thread.join()
