"""Operações de catálogo por conta. O WebDriver interage só com controles visíveis."""
import re
import time
from pathlib import Path
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

DOM = Path(__file__).with_name("catalogo_dom.js").read_text(encoding="utf8")
CAIXA = "Caixa Reciclável"


def ler(d, op, raiz=None, nome=None):
    return d.execute_script(DOM, op, raiz, nome)


def esperar(d, fn, segundos=6):
    return WebDriverWait(d, segundos, poll_frequency=.2,
                         ignored_exceptions=(StaleElementReferenceException,)).until(fn)


def painel(d, nome):
    return ler(d, "janela", None, nome)


def rotulo(d, p, nome):
    return ler(d, "rotulo", p, nome)


def abrir(d, nome, seletor):
    p = painel(d, nome)
    if not p:
        esperar(d, lambda _: next((e for e in d.find_elements(By.CSS_SELECTOR, seletor)
                                   if e.is_displayed()), None)).click()
        p = esperar(d, lambda _: painel(d, nome))
    cab = p.find_elements(By.CSS_SELECTOR, ".nitro-card-header")
    if cab:
        cab[0].click()
    return p


def clicar(d, p, nome):
    esperar(d, lambda _: rotulo(d, p, nome)).click()


def pagina(d, *caminho):
    loja = abrir(d, "Habblive Loja", ".icon-catalog")
    clicar(d, loja, caminho[0])
    for i, nome in enumerate(caminho[1:], 1):
        if i+1 < len(caminho) and rotulo(d, loja, caminho[i+1]):
            continue
        clicar(d, loja, nome)
    def carregou(_):
        linhas = [s.strip() for s in loja.text.splitlines() if s.strip()]
        return caminho[-1] in linhas[:3]
    esperar(d, carregou)
    return loja


def disponivel(el):
    return el and el.is_enabled() and not re.search(r"(?:^|\s)disabled(?:\s|$)", el.get_attribute("class") or "") and el.get_attribute("aria-disabled") != "true"


def saldo(d, moeda):
    vs = [c["saldo"] for c in ler(d, "carteiras") if c["nome"] == moeda]
    if len(vs) != 1:
        raise ValueError(f"Saldo {moeda} não identificado")
    return vs[0]


def comprar(d, loja, nome, preco, reservar=None):
    if preco["valor"] <= 0 or saldo(d, preco["moeda"]) < preco["valor"]:
        return False
    if not rotulo(d, loja, nome) or ler(d, "preco", loja) != preco:
        raise ValueError("A oferta mudou antes do clique")
    anterior = saldo(d, preco["moeda"])
    if reservar and not reservar():
        return False
    clicar(d, loja, "Comprar")
    # Algumas preferências do cliente dispensam a janela de confirmação.
    def mudou(_):
        return painel(d, "Comprar") or (saldo(d, preco["moeda"]) < anterior)
    modal = esperar(d, mudou)
    if modal is not True:
        if not rotulo(d, modal, nome) or ler(d, "preco", modal) != preco:
            cancelar = rotulo(d, modal, "Cancelar")
            if cancelar:
                cancelar.click()
            raise ValueError("Nome, preço ou moeda divergentes na confirmação")
        botoes = modal.find_elements(By.CSS_SELECTOR, "button,.btn,[role='button']")
        botao = next((e for e in botoes if e.text.strip()=="Comprar" and disponivel(e)), None)
        if not botao:
            raise ValueError("Confirmação Comprar indisponível")
        botao.click()
        esperar(d, lambda _: saldo(d, preco["moeda"]) < anterior, 8)
    time.sleep(1.05)
    return True


def selecionar(d, loja, indice):
    itens = ler(d, "opcoes", loja)
    if indice >= len(itens):
        raise ValueError("A lista de ofertas mudou")
    itens[indice].click()
    time.sleep(.25)
    esperar(d, lambda _: rotulo(d, loja, "Comprar"))


def selecionar_cambio(d, loja, indice, moeda):
    selecionar(d, loja, indice)
    padrao = re.compile(r"\b(?:vale\s+)?([0-9.]+)\s+"+moeda+r"\b", re.I)
    def oferta(_):
        for nome in loja.text.splitlines():
            m = padrao.search(nome)
            if m:
                preco = ler(d, "preco", loja)
                if preco and preco["moeda"] == moeda:
                    return (int(m[1].replace(".","")), nome, preco)
        return False
    return esperar(d, oferta)


def cambio(d, moeda):
    if moeda not in ("Duckets", "Diamantes"):
        raise ValueError("Moeda inválida")
    loja = pagina(d, "Economia", "Câmbios", moeda)
    itens = esperar(d, lambda _: ler(d, "opcoes", loja))
    ofertas = []
    for i in range(len(itens)):
        valor, nome, preco = selecionar_cambio(d, loja, i, moeda)
        ofertas.append((valor, i, nome, preco))
    for _, i, nome, preco in sorted(ofertas, key=lambda o: -o[0]):
        if saldo(d, moeda) < preco["valor"]:
            continue
        atual = selecionar_cambio(d, loja, i, moeda)
        if atual[1:] != (nome, preco):
            raise ValueError("Mudança de oferta; consulte o catálogo novamente")
        while saldo(d, moeda) >= preco["valor"]:
            if not comprar(d, loja, nome, preco):
                break


def inventario_caixa(d):
    inv = abrir(d, "Inventário", ".icon-inventory,.icon-inventory-furni")
    if "Esta categoria está vazia!" in inv.text:
        return inv, 0
    campo = esperar(d, lambda _: ler(d, "busca", inv))
    campo.clear()
    campo.send_keys(CAIXA, Keys.ENTER)
    busca = inv.find_elements(By.CSS_SELECTOR, "[title='Pesquisar'],[aria-label='Pesquisar']")
    if busca:
        busca[0].click()
    time.sleep(.35)
    pilhas = ler(d, "opcoes", inv)
    if not pilhas:
        if re.search(r"\b0\s+Mobis|nenhum|vazia",inv.text,re.I):
            return inv, 0
        raise ValueError("Não foi possível identificar o resultado da busca no inventário")
    for pilha in pilhas:
        pilha.click()
        time.sleep(.2)
        if rotulo(d, inv, CAIXA):
            n = re.search(r"\b\d+\b", pilha.text)
            return inv, int(n[0]) if n else 1
    return inv, 0


def reciclar(d, ciclos):
    for _ in range(ciclos):
        loja = pagina(d, "Especiais", "Furni-Matic", "Reciclar")
        inv, quant = inventario_caixa(d)
        if quant < 8:
            loja = pagina(d, "Especiais", "Furni-Matic", "Caixas Recicladas")
            itens = esperar(d, lambda _: ler(d, "opcoes", loja))
            pacotes = []
            for i, item in enumerate(itens):
                n = re.search(r"\b\d+\b", item.text)
                pacotes.append((int(n[0]) if n else 1, i))
            comprou = False
            for qtd, i in sorted(pacotes, key=lambda v:(v[0]!=16,-v[0])):
                selecionar(d, loja, i)
                preco = ler(d, "preco", loja)
                if (qtd+quant >= 8 and rotulo(d, loja, CAIXA) and preco and
                        preco["moeda"]=="Duckets" and comprar(d, loja, CAIXA, preco)):
                    comprou = True
                    break
            if not comprou:
                raise ValueError("Nenhum pacote de caixas pagável em Duckets")
            loja = pagina(d, "Especiais", "Furni-Matic", "Reciclar")
            inv, quant = inventario_caixa(d)
        if quant < 8:
            raise ValueError("Oito caixas ainda não disponíveis no inventário")
        for i in range(8):
            if not rotulo(d, inv, CAIXA) or disponivel(rotulo(d, loja, "Iniciar reciclagem")):
                raise ValueError("Seleção ou conteúdo da máquina inesperado")
            clicar(d, inv, "Reciclar item")
            time.sleep(1)
        abrir(d, "Habblive Loja", ".icon-catalog")
        iniciar = esperar(d, lambda _: disponivel(rotulo(d, loja, "Iniciar reciclagem")))
        clicar(d, loja, "Iniciar reciclagem")
        esperar(d, lambda _: not disponivel(rotulo(d, loja, "Iniciar reciclagem")))
