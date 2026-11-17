import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

VOUCHER_ATIVO = True


def texto(raiz, nome):
    # Os rótulos desta interface são constantes definidos no código.
    for el in raiz.find_elements(By.XPATH, f".//*[normalize-space(text())='{nome}']"):
        if el.is_displayed():
            return el
    return False


def janela(driver, titulo):
    for painel in driver.find_elements(By.CSS_SELECTOR, ".nitro-card"):
        if painel.is_displayed() and titulo.casefold() in painel.text.casefold().splitlines()[:3]:
            return painel
    return False


def abrir_loja(driver):
    espera = WebDriverWait(driver, 5)
    loja = janela(driver, "Habblive Loja")
    if not loja:
        espera.until(lambda d: d.find_element(By.CSS_SELECTOR, ".navigation-item.icon-catalog")).click()
        loja = espera.until(lambda d: janela(d, "Habblive Loja"))
    return loja


def voucher(driver, codigo):
    if not VOUCHER_ATIVO or not re.fullmatch(r"[A-Z0-9]{4,40}", codigo):
        raise ValueError("Voucher desativado ou inválido")
    loja = abrir_loja(driver)
    espera = WebDriverWait(driver, 6)
    if not texto(loja, "Gerar código:"):
        texto(loja, "Destaque").click()
    rotulo = espera.until(lambda d: texto(loja, "Gerar código:"))
    campo = rotulo.find_element(By.XPATH, "./ancestor::*[.//input][1]//input")
    campo.clear()
    campo.send_keys(codigo)
    espera.until(lambda d: texto(loja, "Gerar")).click()
    def retorno(d):
        for painel in d.find_elements(By.CSS_SELECTOR, ".nitro-alert, .nitro-card"):
            if painel != loja and painel.is_displayed() and texto(painel, "Fechar"):
                if re.search(r"voucher|código|resgat|cupom", painel.text, re.I):
                    return texto(painel, "Fechar")
        return False
    resposta = espera.until(retorno)
    resposta.click()
    loja.find_element(By.CSS_SELECTOR, ".nitro-card-header-close").click()


def calendario(driver):
    for painel in driver.find_elements(By.CSS_SELECTOR, '[class*="advent-calendar"], [class*="gift-calendar"]'):
        if not painel.is_displayed():
            continue
        abrir = texto(painel, "Abrir")
        if abrir:
            abrir.click()
            botoes = painel.find_elements(By.CSS_SELECTOR, ".nitro-card-header-close")
            if botoes:
                botoes[0].click()
            return True
    return False


def aceitar_amigos(driver):
    espera = WebDriverWait(driver, 5)
    def painel(d):
        return next((el for el in d.find_elements(By.CSS_SELECTOR, ".nitro-friend-list, [class*='friend-list'].nitro-card")
                     if el.is_displayed()), False)
    if not painel(driver):
        espera.until(lambda d: d.find_element(By.CSS_SELECTOR, ".navigation-item.icon-friendall, .navigation-item.icon-friend-list")).click()
    lista = espera.until(painel)
    aba = texto(lista, "Pedidos") or texto(lista, "Pedidos de amizade")
    if aba:
        aba.click()
    else:
        raise ValueError("Aba de pedidos indisponível")
    todos = texto(lista, "Aceitar todos")
    if todos:
        todos.click()
        return
    while True:
        aceitar = texto(lista, "Aceitar")
        if not aceitar:
            return
        antigo = lista.text
        aceitar.click()
        espera.until(lambda d: lista.text != antigo)
