import re
import time
from pathlib import Path
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from interface_jogo import texto


CHOOSER_JS = Path(__file__).with_name("chooser_acoes.js").read_text(encoding="utf8")


def negociar(driver, usuario, enviar_chat, registrar=print):
    configuracao = getattr(driver.command_executor, "client_config", None)
    timeout_original = configuracao.timeout if configuracao is not None else None
    etapa = "iniciando"
    inicio = time.monotonic()

    def progresso(descricao):
        nonlocal etapa
        etapa = descricao
        registrar(f"NEGOCIAR: {descricao}")
        if time.monotonic() - inicio > 240:
            raise TimeoutError("Prazo total de 240 segundos atingido")

    try:
        if configuracao is not None:
            configuracao.timeout = 25
        _negociar(driver, usuario, enviar_chat, progresso)
    except Exception as erro:
        raise RuntimeError(
            f"NEGOCIAR interrompido na etapa '{etapa}': "
            f"{type(erro).__name__}: {str(erro)[:350]}. "
            "Confira a janela de troca antes de reenviar o comando."
        ) from erro
    finally:
        if configuracao is not None:
            configuracao.timeout = timeout_original


def _negociar(driver, usuario, enviar_chat, progresso):
    if usuario.casefold() == "solitudine":
        raise ValueError("A conta de destino é a própria conta")
    espera = WebDriverWait(driver, 6)
    def painel_troca(d):
        for p in d.find_elements(By.CSS_SELECTOR, ".nitro-card"):
            partes = " ".join(p.text.split()).casefold()
            if (p.is_displayed() and p.find_elements(By.ID, "inventory-trade-quantity")
                    and f"{usuario.casefold()} está oferecendo" in partes
                    and re.search(r"(?:^|\s)solitudine está oferecendo(?:\s|$)", partes)):
                return p
        return False

    progresso("verificando negociação já aberta")
    if driver.execute_script("return !!document.getElementById('inventory-trade-quantity')"):
        raise ValueError("Já existe uma negociação aberta")
    progresso("abrindo chooser pelo chat")
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
        acao.el.click(); return true;
    """))
    progresso("aguardando janela de troca com Solitudine")
    painel = espera.until(painel_troca)
    def contagem():
        titulos = painel.find_elements(By.CSS_SELECTOR, "[title], [aria-label]")
        for el in titulos:
            for atributo in ("title", "aria-label"):
                m = re.fullmatch(r"(\d+) mobis? e \d+ pixels?", el.get_attribute(atributo) or "", re.I)
                if m:
                    return int(m[1])
        raise ValueError("Total de mobis oferecidos indisponível")
    # Após oferecer o lote inteiro a interface seleciona o próximo lote disponível.
    total = contagem()
    while True:
        progresso(f"oferecendo inventário; {total} mobis confirmados na oferta")
        campo = painel.find_element(By.ID, "inventory-trade-quantity")
        botao = texto(painel, "Oferecer")
        if not botao or not campo.is_enabled() or "Selecione um mobi" in painel.text:
            break
        if not painel_troca(driver):
            raise ValueError("Os participantes mudaram")
        quantidade = int(campo.get_attribute("max") or 1)
        if quantidade < 1:
            break
        driver.execute_script("""
            const campo = arguments[0];
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                .set.call(campo, String(arguments[1]));
            campo.dispatchEvent(new Event('input', {bubbles:true}));
            campo.dispatchEvent(new Event('change', {bubbles:true}));
        """, campo, quantidade)
        botao = espera.until(lambda _: texto(painel, "Oferecer"))
        driver.execute_script("arguments[0].click()", botao)
        anterior = total
        total = espera.until(lambda _: (n if (n := contagem()) > anterior else False))
    if total == 0:
        progresso("inventário sem mobis oferecidos; encerrando troca vazia")
        cancelar = texto(painel, "Cancelar negociação")
        if cancelar:
            driver.execute_script("arguments[0].click()", cancelar)
        return
    if not painel_troca(driver):
        raise ValueError("Destinatário indisponível")
    progresso(f"aceitando oferta de {total} mobis para Solitudine")
    aceitar = espera.until(lambda _: texto(painel, "Aceitar negociação"))
    driver.execute_script("arguments[0].click()", aceitar)
    for p in driver.find_elements(By.CSS_SELECTOR, ".nitro-card, .nitro-alert"):
        if p.is_displayed() and "O outro Habbo não ofereceu nada a você" in p.text:
            fechar = texto(p, "Fechar")
            if fechar:
                driver.execute_script("arguments[0].click()", fechar)
    def confirmar(d):
        for aviso in d.find_elements(By.CSS_SELECTOR, ".nitro-card, .nitro-alert"):
            if aviso.is_displayed() and "O outro Habbo não ofereceu nada a você" in aviso.text:
                fechar = texto(aviso, "Fechar")
                if fechar:
                    d.execute_script("arguments[0].click()", fechar)
        p = painel_troca(d)
        if not p:
            return False
        botao = (texto(p, "Complete the trade") or texto(p, "Confirmar negociação")
                 or texto(p, "Concluir negociação"))
        return botao if botao and botao.is_enabled() and "disabled" not in (
            botao.get_attribute("class") or ""
        ).split() else False
    progresso("aguardando Solitudine aceitar para confirmar a troca")
    confirmacao = WebDriverWait(driver, 45).until(confirmar)
    progresso("confirmando troca")
    driver.execute_script("arguments[0].click()", confirmacao)
    progresso("aguardando fechamento da troca")
    WebDriverWait(driver, 45).until(lambda d: not painel_troca(d))
    progresso("janela da troca fechada após confirmação")
