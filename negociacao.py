import time
from pathlib import Path
from selenium.webdriver.support.ui import WebDriverWait


CHOOSER_JS = Path(__file__).with_name("chooser_acoes.js").read_text(encoding="utf8")

# Consultas curtas ao DOM. Os nomes e contadores pertencem ao painel de troca,
# independentemente de a janela externa usar nitro-card ou nitro-card-modern.
TROCA_JS = r"""
const [operacao, usuario, argumento] = arguments;
const normal = valor => String(valor || '').normalize('NFC').trim().toLowerCase();
const visivel = el => el && el.isConnected && el.getClientRects().length &&
    getComputedStyle(el).visibility !== 'hidden';
const habilitado = el => visivel(el) && !el.disabled &&
    !el.matches('.disabled, .is-disabled, [aria-disabled="true"]');
const paineis = [...document.querySelectorAll('.inventory-trade-view')].filter(visivel);
if (!paineis.length) return {aberta: false};
if (paineis.length !== 1) throw Error('Mais de uma janela de troca visível');
const painel = paineis[0];
const proprio = painel.querySelector('.inventory-trade-view__user-offer.is-own .inventory-trade-view__user-name');
const outro = painel.querySelector('.inventory-trade-view__user-offer.is-other .inventory-trade-view__user-name');
if (normal(proprio?.textContent) !== normal(usuario) || normal(outro?.textContent) !== 'solitudine')
    throw Error('Participantes da troca não conferem: ' + proprio?.textContent + ' / ' + outro?.textContent);
const botao = (raiz, nomes) => [...raiz.querySelectorAll('.btn,button')]
    .find(el => nomes.includes(normal(el.textContent)) && habilitado(el));
const setter = (el, valor) => {
    const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, String(valor));
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
};
const top = painel.querySelector('.inventory-trade-view__top');
const itens = () => [...top.querySelectorAll('.layout-grid-item')];
const chave = (el, indice) => indice + '|' + el.style.backgroundImage + '|' +
    (el.querySelector('.nitro-item-count')?.textContent || '1');
const pagina = () => painel.querySelector('.inventory-furniture-view__page-button.is-active')?.textContent.trim() || '1';
const proxima = () => painel.querySelector('.inventory-furniture-view__page-arrow-icon--right')?.closest('button');
const quantidade = () => painel.querySelector('#inventory-trade-quantity');
const oferecer = () => botao(top, ['oferecer']);
const acoes = painel.querySelector('.inventory-trade-view__actions');
const confirmar = () => botao(acoes, ['complete the trade', 'confirmar negociação', 'concluir negociação']);
const contagem = () => {
    const primeiro = painel.querySelector('.inventory-trade-view__trade-panel');
    const titulo = primeiro?.querySelector('.inventory-trade-view__trade-count-row')?.getAttribute('title');
    const match = String(titulo || '').match(/^([\d.,\s]+)\s+mobis?\s+e\s+[\d.,\s]+\s+pixels?$/i);
    if (!match) throw Error('Contador da própria oferta indisponível');
    return Number(match[1].replace(/\D/g, ''));
};
if (operacao === 'limpar_filtro') {
    const busca = top.querySelector('.inventory-furniture-view__search-input');
    const filtro = top.querySelector('select');
    if (busca?.value) setter(busca, '');
    const todos = filtro && [...filtro.options].find(o => normal(o.textContent) === 'todos');
    if (todos && filtro.value !== todos.value) setter(filtro, todos.value);
    top.querySelector('.inventory-furniture-view__page-button')?.click();
} else if (operacao === 'selecionar') {
    const item = itens().find((el, i) => chave(el, i) === argumento);
    if (!item) return {mudou: true};
    item.click();
} else if (operacao === 'quantidade') {
    const campo = quantidade();
    const maximo = Number(campo?.max);
    if (!habilitado(campo) || !Number.isInteger(maximo) || maximo < 1 || !oferecer())
        throw Error('Lote selecionado indisponível para oferecer');
    setter(campo, maximo);
} else if (operacao === 'oferecer') {
    const campo = quantidade();
    const ativo = itens().find(el => el.classList.contains('active'));
    const indice = itens().indexOf(ativo);
    if (!ativo || chave(ativo, indice) !== argumento || !oferecer() ||
        !habilitado(campo) || Number(campo.value) !== Number(campo.max))
        throw Error('Seleção ou quantidade mudou antes da oferta');
    oferecer().click();
} else if (operacao === 'proxima') {
    if (!habilitado(proxima())) throw Error('Próxima página indisponível');
    proxima().click();
} else if (operacao === 'aceitar') {
    if (contagem() !== argumento || argumento <= 0) throw Error('Oferta mudou antes do aceite');
    const aceitar = botao(acoes, ['aceitar negociação']);
    if (!aceitar) throw Error('Aceitar negociação indisponível');
    aceitar.click();
} else if (operacao === 'confirmar') {
    if (contagem() !== argumento || argumento <= 0) throw Error('Oferta mudou antes da confirmação');
    if (!confirmar()) throw Error('Confirmação indisponível');
    confirmar().click();
} else if (operacao === 'cancelar_vazia') {
    if (contagem() !== 0) throw Error('A oferta contém mobis');
    const cancelar = botao(acoes, ['cancelar negociação']);
    if (!cancelar) throw Error('Cancelar negociação indisponível');
    cancelar.click();
} else if (operacao === 'aviso') {
    for (const aviso of document.querySelectorAll('.nitro-card, .nitro-card-modern, .nitro-alert')) {
        if (visivel(aviso) && aviso.textContent.includes('O outro Habbo não ofereceu nada a você'))
            botao(aviso, ['fechar'])?.click();
    }
}
if (operacao !== 'estado') return {executado: true};
const lista = itens();
const ativo = lista.find(el => el.classList.contains('active'));
return {aberta: true, total: contagem(), pagina: pagina(),
    proxima: !!habilitado(proxima()), itens: lista.map(chave),
    selecionado: ativo ? chave(ativo, lista.indexOf(ativo)) : null,
    podeOferecer: !!oferecer() && !!habilitado(quantidade()) && Number(quantidade()?.max) > 0,
    maximo: Number(quantidade()?.max || 0), quantidade: Number(quantidade()?.value || 0),
    podeConfirmar: !!confirmar()};
"""


def negociar(driver, usuario, enviar_chat, registrar=print):
    configuracao = getattr(driver.command_executor, "client_config", None)
    timeout_original = configuracao.timeout if configuracao is not None else None
    etapa = "iniciando"
    inicio = time.monotonic()

    def progresso(descricao):
        nonlocal etapa
        etapa = descricao
        registrar(f"NEGOCIAR: {descricao}")
        if time.monotonic() - inicio > 600:
            raise TimeoutError("Prazo total de 600 segundos atingido")

    try:
        if configuracao is not None:
            configuracao.timeout = 25
        _transferir(driver, usuario, enviar_chat, progresso)
    except Exception as erro:
        raise RuntimeError(
            f"NEGOCIAR interrompido na etapa '{etapa}': "
            f"{type(erro).__name__}: {str(erro)[:350]}. "
            "Confira a janela de troca antes de reenviar o comando."
        ) from erro
    finally:
        if configuracao is not None:
            configuracao.timeout = timeout_original


def _abrir_troca(driver, usuario, enviar_chat, progresso):
    if usuario.casefold() == "solitudine":
        raise ValueError("A conta de destino é a própria conta")
    espera = WebDriverWait(driver, 6)
    progresso("verificando negociação já aberta")
    estado = driver.execute_script(TROCA_JS, "estado", usuario, None)
    if estado["aberta"]:
        progresso("retomando troca aberta com os participantes conferidos")
        return
    progresso("abrindo chooser pelo chat")
    if not driver.execute_script(CHOOSER_JS + "return !!seletor();"):
        enviar_chat(":chooser")
    progresso("filtrando Solitudine no chooser")
    espera.until(lambda d: d.execute_script(CHOOSER_JS + """
        if (!seletor() || !campo()) return false;
        valor(seletor(), 'live'); valor(campo(), 'Solitudine'); return true;
    """))
    progresso("selecionando Solitudine pelo nome")
    espera.until(lambda d: d.execute_script(CHOOSER_JS + """
        const tr = linhas().find(e => normal(nomeLinha(e)) === 'solitudine');
        if (!tr) return false;
        tr.click(); return true;
    """))
    progresso("clicando Negociar no menu de Solitudine")
    espera.until(lambda d: d.execute_script(CHOOSER_JS + """
        const acao = opcao(menu('Solitudine'), /^negociar$/);
        if (!acao) return false;
        acao.el.click(); fechar(); return true;
    """))
    progresso("aguardando janela de troca com Solitudine")
    espera.until(lambda d: d.execute_script(TROCA_JS, "estado", usuario, None)["aberta"])


def _transferir(driver, usuario, enviar_chat, progresso):
    espera = WebDriverWait(driver, 8, poll_frequency=0.25)

    def executar(acao="estado", argumento=None):
        return driver.execute_script(TROCA_JS, acao, usuario, argumento)

    def estado():
        atual = executar()
        if not atual["aberta"]:
            raise RuntimeError("A janela da troca foi fechada antes da confirmação")
        return atual

    transferidos = 0
    while True:
        _abrir_troca(driver, usuario, enviar_chat, progresso)
        executar("limpar_filtro")
        ignorados = set()
        while True:
            atual = estado()
            progresso(f"oferecendo inventário; {atual['total']} mobis na oferta, página {atual['pagina']}")
            pendentes = [chave for chave in atual["itens"]
                         if (atual["pagina"], chave) not in ignorados]
            if not pendentes:
                if not atual["proxima"]:
                    break
                executar("proxima")
                espera.until(lambda _: estado()["pagina"] != atual["pagina"])
                continue

            chave = pendentes[0]
            resposta = executar("selecionar", chave)
            if resposta.get("mudou"):
                continue
            selecionado = espera.until(lambda _: (
                e if (e := estado())["selecionado"] == chave else False
            ))
            if not selecionado["podeOferecer"]:
                ignorados.add((selecionado["pagina"], chave))
                continue
            executar("quantidade")
            espera.until(lambda _: (
                e if (e := estado())["quantidade"] == selecionado["maximo"]
                and e["selecionado"] == chave else False
            ))
            anterior = selecionado["total"]
            executar("oferecer", chave)
            confirmado = espera.until(lambda _: (
                e if (e := estado())["total"] > anterior else False
            ))
            progresso(f"oferta confirmada: +{confirmado['total'] - anterior} mobis; total {confirmado['total']}")
            # A retirada pode mudar os índices e reduzir a quantidade de páginas.
            # Recomece na primeira página para também pegar os lotes restantes.
            executar("limpar_filtro")
            ignorados.clear()

        total = estado()["total"]
        if total == 0:
            progresso(f"nenhum lote disponível para oferecer; {transferidos} mobis em trocas confirmadas")
            executar("cancelar_vazia")
            espera.until(lambda _: not executar()["aberta"])
            return
        progresso(f"aceitando oferta de {total} mobis para Solitudine")
        executar("aceitar", total)
        progresso("aguardando Solitudine aceitar para confirmar a troca")

        def confirmar_disponivel(_):
            executar("aviso")
            return estado()["podeConfirmar"]

        WebDriverWait(driver, 60, poll_frequency=0.5).until(confirmar_disponivel)
        progresso(f"confirmando oferta de {total} mobis")
        executar("confirmar", total)
        progresso("aguardando fechamento após confirmação")
        WebDriverWait(driver, 60, poll_frequency=0.5).until(lambda _: not executar()["aberta"])
        transferidos += total
        progresso(f"troca confirmada com {total} mobis; verificando lotes restantes")
