import re
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from interface_jogo import texto


def negociar(driver, usuario):
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

    if driver.find_elements(By.ID, "inventory-trade-quantity"):
        raise ValueError("Já existe uma negociação aberta")
    chat = espera.until(lambda d: d.find_element(By.CSS_SELECTOR, "input.chat-input"))
    chat.clear()
    chat.send_keys(":chooser", Keys.ENTER)
    def chooser(d):
        for p in d.find_elements(By.CSS_SELECTOR, ".nitro-card"):
            if p.is_displayed() and "Lista de Habbos" in p.text.splitlines()[:3]:
                return p
        return False
    lista = espera.until(chooser)
    campo = lista.find_element(By.CSS_SELECTOR, "input[placeholder='Digite para procurar...']")
    campo.clear()
    campo.send_keys("Solitudine")
    espera.until(lambda _: texto(lista, "Solitudine")).click()
    def menu_alvo(d):
        for menu in d.find_elements(By.CSS_SELECTOR, ".nitro-context-menu.visible"):
            if menu.is_displayed() and menu.text.splitlines()[:1] == ["Solitudine"]:
                return texto(menu, "Negociar")
        return False
    espera.until(menu_alvo).click()
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
        campo = painel.find_element(By.ID, "inventory-trade-quantity")
        botao = texto(painel, "Oferecer")
        if not botao or not campo.is_enabled() or "Selecione um mobi" in painel.text:
            break
        if not painel_troca(driver):
            raise ValueError("Os participantes mudaram")
        quantidade = int(campo.get_attribute("max") or 1)
        if quantidade < 1:
            break
        campo.send_keys(Keys.CONTROL, "a")
        campo.send_keys(str(quantidade))
        botao.click()
        anterior = total
        total = espera.until(lambda _: (n if (n := contagem()) > anterior else False))
    if total == 0:
        cancelar = texto(painel, "Cancelar negociação")
        if cancelar:
            cancelar.click()
        return
    if not painel_troca(driver):
        raise ValueError("Destinatário indisponível")
    espera.until(lambda _: texto(painel, "Aceitar negociação")).click()
    for p in driver.find_elements(By.CSS_SELECTOR, ".nitro-card, .nitro-alert"):
        if p.is_displayed() and "O outro Habbo não ofereceu nada a você" in p.text:
            fechar = texto(p, "Fechar")
            if fechar:
                fechar.click()
    def confirmar(d):
        p = painel_troca(d)
        return p and texto(p, "Complete the trade")
    WebDriverWait(driver, 45).until(confirmar).click()
    WebDriverWait(driver, 45).until(lambda d: not painel_troca(d))
