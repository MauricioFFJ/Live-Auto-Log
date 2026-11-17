"""Rodízio de compras no catálogo dominical, no próprio navegador da conta."""
from collections import deque
from datetime import datetime
import time
from zoneinfo import ZoneInfo

import catalogo as loja

BRASILIA = ZoneInfo("America/Sao_Paulo")


class VendaDominical:
    def __init__(self, conta):
        self.conta = conta
        self.data = None
        self.fila = deque()
        self.proxima = 0
        self.esgotados = set()
        self.regulares = set()
        self.ignorados = set()
        self.lote_vip = False
        self.aberta = False
        self.encerrada = False

    def verificar(self, driver, registrar, agora=None):
        if self.conta not in (1, 2) or time.monotonic() < self.proxima:
            return
        agora = agora or datetime.now(BRASILIA)
        if agora.weekday() != 6 or agora.hour < 16:
            return
        if self.data != agora.date():
            self.__init__(self.conta)
            self.data = agora.date()
        if not self.lote_vip and (agora.hour, agora.minute) >= (16, 10):
            self.lote_vip = True
            self.aberta = False
            self.encerrada = False
            self.fila.clear()
            self.ignorados.clear()
            self.esgotados.clear()
        if self.encerrada:
            if self.lote_vip and (agora.hour, agora.minute) < (16, 20):
                self.encerrada = False
                self.aberta = False
            else:
                return
        try:
            self.passo(driver, registrar)
        except Exception as erro:
            registrar(f"Catálogo será relido em 5 s: {erro!r}")
            if self.fila:
                self.fila.popleft()
            self.aberta = False
            self.proxima = time.monotonic() + 5

    def passo(self, driver, registrar):
        aviso = loja.ler(driver, "avisoAtualizacao")
        if aviso:
            aviso.click()
            self.aberta = False
        painel = loja.painel(driver, "Habblive Loja") if self.aberta else None
        if not painel or not loja.ler(driver, "paginaLimitada", painel):
            painel = loja.pagina(driver, "Economia", "Edição Limitada")
            self.aberta = True
        ofertas = loja.ler(driver, "raros", painel)
        if not ofertas:
            self.proxima = time.monotonic() + 5
            self.aberta = False
            return
        chaves = [o["id"] for o in ofertas]
        if any(not c for c in chaves) or len(set(chaves)) != len(chaves):
            raise ValueError("Oferta sem identificador único no catálogo")
        if not self.lote_vip:
            self.regulares.update(o["id"] for o in ofertas if not o["vip"])
        elif not loja.ler(driver, "vip"):
            self.ignorados.update(c for c in chaves if c not in self.regulares)
        if not self.fila:
            self.fila.extend(c for c in chaves if c not in self.esgotados | self.ignorados)
        if not self.fila:
            registrar("Rodada concluída: todas as ofertas listadas estão esgotadas ou restritas ao VIP.")
            self.encerrada = True
            self.proxima = time.monotonic() + 5
            return
        chave = self.fila[0]
        item = next((o for o in ofertas if o["id"] == chave), None)
        if item:
            item["el"].click()
            time.sleep(.25)
            estado = loja.esperar(driver, lambda _: loja.ler(driver, "estadoRaro", painel))
            if estado == "esgotado":
                self.esgotados.add(chave)
                registrar(f"{chave}: ESGOTADO")
            elif item["vip"] and not loja.ler(driver, "vip"):
                self.ignorados.add(chave)
                registrar(f"{chave}: oferta VIP ignorada")
            else:
                nome = loja.ler(driver, "titulo", painel) or item["nome"]
                preco = loja.ler(driver, "preco", painel)
                if not nome or not preco:
                    raise ValueError(f"{chave}: nome ou preço ainda não identificados")
                if loja.comprar(driver, painel, nome, preco):
                    registrar(f"{nome}: compra confirmada pelo débito")
                else:
                    registrar(f"{nome}: saldo insuficiente")
                    self.proxima = time.monotonic() + 5
        self.fila.popleft()
