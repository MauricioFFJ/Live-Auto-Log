import os
import time
import threading
import json
import base64
import queue
import urllib.request
import urllib.error
from datetime import datetime, timezone

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
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
CHECK_INTERVAL = 15  # segundos entre verificações da sessão
COMMAND_POLL_INTERVAL = int(os.getenv("COMMAND_POLL_INTERVAL", "20"))
COMMANDS_PATH = "commands"
COMMAND_MAX_AGE_SECONDS = 600  # comandos com mais de 10 minutos são ignorados
COMMAND_MAX_ATTEMPTS = 5
COMMAND_RETRY_DELAY_SECONDS = max(1, int(os.getenv("COMMAND_RETRY_DELAY_SECONDS", "15")))
CHAT_FIELD_TIMEOUT = max(3, int(os.getenv("CHAT_FIELD_TIMEOUT", "12")))
DEBUG_SESSAO = os.getenv("DEBUG_SESSAO", "1").strip().lower() not in ("0", "false", "no")
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
                if ultima is not None and estado not in ("⏳ Iniciando", "🔄 Relogando"):
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
def github_api_request(path):
    """Consulta a API de conteúdo do repositório onde a fila é gravada."""
    if not GITHUB_REPOSITORY or not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_REPOSITORY ou COMMAND_GITHUB_TOKEN/GITHUB_TOKEN não configurado.")

    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{path}?ref={GITHUB_REF_NAME}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Habblive-Persistent-Login",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def listar_arquivos_comandos():
    resultado = github_api_request(COMMANDS_PATH)
    if resultado is None or not isinstance(resultado, list):
        return []
    return [
        item for item in resultado
        if item.get("type") == "file" and item.get("name", "").endswith(".json")
    ]


def obter_comando(caminho):
    resultado = github_api_request(caminho)
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
    """Digite normalmente e envie ENTER, detalhando cada chamada Selenium."""
    if not mensagem or len(mensagem) > 100:
        raise ValueError("Mensagem vazia ou maior que 100 caracteres.")

    seletor = "input.chat-input[placeholder='Fale aqui...']"
    marcar_etapa(account_id, "FALAR: procurando campo de conversa...", status="💬 FALAR")
    campo = WebDriverWait(driver, CHAT_FIELD_TIMEOUT).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, seletor))
    )
    marcar_etapa(account_id, "FALAR: campo de conversa localizado.", Fore.GREEN)

    marcar_etapa(account_id, "FALAR: clicando no campo...")
    campo.click()
    marcar_etapa(account_id, "FALAR: campo clicado.", Fore.GREEN)

    marcar_etapa(account_id, "FALAR: limpando o campo...")
    campo.clear()
    marcar_etapa(account_id, "FALAR: digitando a mensagem...")
    campo.send_keys(mensagem)
    marcar_etapa(account_id, "FALAR: conferindo texto digitado...")
    texto_digitado = campo.get_attribute("value")
    if texto_digitado != mensagem:
        raise RuntimeError(
            f"Texto digitado diverge do esperado (esperado={len(mensagem)} "
            f"caracteres; campo={len(texto_digitado or '')} caracteres)."
        )
    marcar_etapa(account_id, "FALAR: mensagem digitada e conferida.", Fore.GREEN)

    marcar_etapa(account_id, "FALAR: pressionando ENTER...")
    try:
        campo.send_keys(Keys.ENTER)
    except Exception as erro:
        raise EnterIndeterminado(
            f"Falha no ENTER do comando {comando_id}: {repr(erro)}"
        ) from erro
    marcar_etapa(
        account_id,
        f"FALAR: ENTER pressionado para {comando_id}.",
        Fore.GREEN,
        status="✅ Online",
    )


def processar_comando_da_fila(driver, account_id):
    """Retira no máximo uma ação da fila; Selenium fica na thread da conta."""
    fila = filas_comandos.get(account_id)
    if fila is None:
        return

    try:
        comando_id, mensagem, criado_timestamp, tentativas, executar_apos = fila.get_nowait()
    except queue.Empty:
        return

    agora = time.time()
    if agora < executar_apos:
        fila.put((comando_id, mensagem, criado_timestamp, tentativas, executar_apos))
        return

    if agora - criado_timestamp > COMMAND_MAX_AGE_SECONDS:
        log(f"[Conta {account_id}] FALAR {comando_id} expirou antes da execução.", Fore.YELLOW)
        return

    tentativa_atual = tentativas + 1
    marcar_etapa(
        account_id,
        f"FALAR: iniciando comando {comando_id}, tentativa "
        f"{tentativa_atual}/{COMMAND_MAX_ATTEMPTS}.",
        status="💬 FALAR",
    )

    try:
        enviar_mensagem(driver, account_id, mensagem, comando_id)
        with lock:
            comandos_concluidos.setdefault(comando_id, set()).add(account_id)
        log(
            f"[Conta {account_id}] ✅ Comando {comando_id}: ENTER enviado "
            "(recebimento pelo servidor ainda não confirmado).",
            Fore.GREEN,
        )

    except EnterIndeterminado as erro:
        # Se ocorreu um timeout DURANTE o ENTER, ele talvez tenha sido recebido.
        # Não tentamos novamente automaticamente para evitar fala duplicada.
        marcar_etapa(account_id, "FALAR: resultado do ENTER indeterminado.", Fore.RED,
                     status="⚠️ ENTER incerto")
        log(f"[Conta {account_id}] {repr(erro)}. Não haverá reenvio automático.", Fore.RED)

    except Exception as erro:
        log(
            f"[Conta {account_id}] FALAR {comando_id} falhou na tentativa "
            f"{tentativa_atual}: {type(erro).__name__}: {str(erro)[:450]!r}",
            Fore.YELLOW,
        )
        if isinstance(erro, TimeoutException):
            diagnosticar_chat(driver, account_id)

        idade = time.time() - criado_timestamp
        if tentativa_atual < COMMAND_MAX_ATTEMPTS and idade < COMMAND_MAX_AGE_SECONDS:
            proxima_tentativa = time.time() + COMMAND_RETRY_DELAY_SECONDS
            fila.put((comando_id, mensagem, criado_timestamp,
                      tentativa_atual, proxima_tentativa))
            log(
                f"[Conta {account_id}] FALAR {comando_id} reagendado "
                f"para daqui a {COMMAND_RETRY_DELAY_SECONDS}s "
                f"(tentativa seguinte {tentativa_atual + 1}/{COMMAND_MAX_ATTEMPTS}).",
                Fore.YELLOW,
            )
        else:
            log(
                f"[Conta {account_id}] ❌ FALAR {comando_id} sem sucesso "
                f"após {tentativa_atual} tentativa(s); não será reenviado.",
                Fore.RED,
            )
        marcar_etapa(account_id, "FALAR: tentativa encerrada.", Fore.YELLOW,
                     status="✅ Online", registrar=False)


def monitorar_comandos(account_ids):
    """Busca comandos no GitHub e os distribui; nunca acessa objetos Selenium."""
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        log(
            "Leitor de comandos desativado: configure COMMAND_GITHUB_TOKEN e GITHUB_REPOSITORY.",
            Fore.YELLOW
        )
        return

    conhecidos = set()
    try:
        conhecidos = {item["name"] for item in listar_arquivos_comandos()}
        log(f"Monitor de comandos iniciado para as contas {account_ids}.", Fore.CYAN)
    except Exception as erro:
        log(f"Não foi possível consultar a fila inicialmente: {repr(erro)}", Fore.YELLOW)

    pendentes = {}
    account_ids_set = set(account_ids)

    while True:
        try:
            arquivos = listar_arquivos_comandos()

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

                    if comando.get("action") != "say":
                        continue

                    mensagem = str(comando.get("message", ""))
                    alvos = set(int(item) for item in comando.get("targets", []))
                    alvos_locais = alvos.intersection(account_ids_set)

                    if not mensagem or len(mensagem) > 100 or not alvos_locais:
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
                        "message": mensagem,
                        "targets": alvos_locais,
                        "dispatched": set(),
                        "created_at": criado_em,
                    }
                    with lock:
                        comandos_concluidos.setdefault(comando_id, set())

                    log(
                        f"Novo comando FALAR recebido ({comando_id}) "
                        f"para contas {sorted(alvos_locais)}.",
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

        except Exception as erro:
            log(f"Erro no monitor de comandos: {repr(erro)}", Fore.RED)

        time.sleep(COMMAND_POLL_INTERVAL)


# ---------- Sessão individual de cada conta ----------
def verificar_sessao(driver, index, cliente_ausente_desde):
    """
    Verificação curta: não fica 90s num WebDriverWait enquanto há fala na fila.
    Retorna (continuar_sessao, momento_em_que_UI_sumiu).
    """
    marcar_etapa(index, "Sessão: consultando URL...", registrar=DEBUG_SESSAO)
    url_atual = driver.current_url
    marcar_etapa(index, "Sessão: consultando interface principal...", registrar=DEBUG_SESSAO)

    try:
        driver.find_element(
            By.CSS_SELECTOR,
            ".cursor-pointer.navigation-item.icon.icon-rooms",
        )
    except NoSuchElementException:
        if url_atual.rstrip("/") != URL_BIGCLIENT.rstrip("/"):
            log(
                f"[Conta {index}] ⚠️ Saiu do Big Client ({url_atual}). Relogando...",
                Fore.YELLOW,
            )
            return False, cliente_ausente_desde

        if cliente_ausente_desde is None:
            cliente_ausente_desde = time.monotonic()
            log(
                f"[Conta {index}] ⚠️ Interface principal não localizada; "
                "monitoramento continuará sem bloquear o FALAR.",
                Fore.YELLOW,
            )
        tempo_ausente = time.monotonic() - cliente_ausente_desde
        marcar_etapa(index, "Sessão: interface temporariamente ausente.",
                     Fore.YELLOW, status="🔄 Cliente carregando", registrar=False)
        if tempo_ausente >= 90:
            log(
                f"[Conta {index}] ❌ Interface indisponível por "
                f"{tempo_ausente:.0f}s. Relogando...",
                Fore.RED,
            )
            return False, cliente_ausente_desde
        return True, cliente_ausente_desde

    # O navegador respondeu e o ícone de quartos está presente.
    if cliente_ausente_desde is not None:
        log(f"[Conta {index}] ✅ Interface do cliente recarregada.", Fore.GREEN)
        if EXECUTAR_ACOES:
            executar_acoes_no_quarto(driver, index)

    marcar_etapa(index, "Sessão: verificação concluída.", Fore.GREEN,
                 status="✅ Online", registrar=DEBUG_SESSAO)
    return True, None


def iniciar_sessao(username, password, index):
    time.sleep(index * 3)  # mantém o espaçamento já utilizado entre logins

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
            # Verificações de sessão acontecem a cada CHECK_INTERVAL;
            # enquanto isso, a fila FALAR é consultada a cada segundo.
            proxima_verificacao = time.monotonic() + CHECK_INTERVAL
            cliente_ausente_desde = None

            while True:
                # PRIORIDADE: registra a tentativa ANTES de qualquer checagem periódica.
                processar_comando_da_fila(driver, index)

                if time.monotonic() >= proxima_verificacao:
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
