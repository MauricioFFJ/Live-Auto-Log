"""Comandos recebidos pela interface do cliente."""
from pathlib import Path
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

MONITOR = Path(__file__).with_name("mencoes.js").read_text(encoding="utf-8")


def interpretar(texto, destinatario=None):
    texto = re.sub(r"\s+Clique aqui para ir ao quarto\.$", "", texto.strip(), flags=re.I)
    if texto.startswith("@"):
        alvo, _, texto = texto.partition(" ")
        if not destinatario or alvo[1:].casefold() != destinatario.casefold():
            return None
    verbo, _, arg = texto.strip().partition(" ")
    verbo, arg = verbo.casefold(), arg.strip()
    if verbo == "andar":
        modo, _, alvo = arg.partition(" ")
        if (modo == "mobi" and re.fullmatch(r"[0-9]{1,16}", alvo)) or (modo == "frente" and 0 < len(alvo) <= 60):
            return "andar", {"modo": modo, "alvo": alvo}
    if verbo in ("cambio", "câmbio") and arg.capitalize() in ("Duckets", "Diamantes"):
        return "cambio", arg.capitalize()
    if verbo == "reciclar" and (not arg or re.fullmatch(r"[1-9][0-9]?|100", arg)):
        return "reciclar", int(arg or "1")
    if verbo == "talk" and 0 < len(arg) <= 100 and not arg.startswith(":"):
        return "say", arg
    if verbo in ("respect", "respeitar") and 0 < len(arg) <= 60:
        return "respect", arg
    if verbo == "voucher" and re.fullmatch(r"[A-Z0-9]{4,40}", arg):
        return "voucher", arg
    if verbo == "visitar":
        dono, sep, quarto = arg.partition("|")
        if sep and 0 < len(dono.strip()) <= 60 and 0 < len(quarto.strip()) <= 100:
            return "visit_room", {"owner": dono.strip(), "room": quarto.strip()}
    if not arg:
        return {"trade": ("trade", ""), "amigos": ("friends", ""), "hotel": ("go_home", "Vista do hotel"), "cafofo": ("go_home", "Cafofo"),
                "carinhos": ("treat_pets", ""), "entrargrupo": ("group", "entrar"),
                "sairgrupo": ("group", "sair")}.get(verbo)
    return None


def grupo(driver, acao):
    rotulos = {"entrar": ("Juntar-se ao Grupo", "Junte-se ao Grupo"), "sair": ("Sair do Grupo",)}
    def botao(raiz, nomes):
        for nome in nomes:
            for el in raiz.find_elements(By.XPATH, f".//*[normalize-space(text())='{nome}']"):
                if el.is_displayed() and el.is_enabled():
                    return el
        return False
    espera = WebDriverWait(driver, 6)
    espera.until(lambda d: botao(d, rotulos[acao])).click()
    if acao == "sair":
        pergunta = "Tem certeza de que deseja sair do Grupo?"
        dialogo = espera.until(lambda d: next((p for p in d.find_elements(
            By.CSS_SELECTOR, ".nitro-card, .nitro-alert, [role='dialog']"
        ) if p.is_displayed() and pergunta in p.text), False))
        espera.until(lambda _: botao(dialogo, ("Aceitar",))).click()


GEOMETRIA = Path(__file__).with_name("andar.js").read_text(encoding="utf8")


def andar(driver, pedido, usuario):
    from selenium.webdriver.common.action_chains import ActionChains
    argumentos = (pedido["modo"], pedido["alvo"], usuario)
    destino = driver.execute_script(GEOMETRIA, *argumentos, False)
    if destino.get("chegou"):
        return
    ActionChains(driver).move_to_element_with_offset(
        destino["canvas"], destino["dx"], destino["dy"]
    ).click().perform()
    WebDriverWait(driver, 12).until(
        lambda d: d.execute_script(GEOMETRIA, *argumentos, True) == [destino["x"], destino["y"]]
    )
