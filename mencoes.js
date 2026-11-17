// Recebe apenas avisos apresentados pela interface de notificações.
if (!window.monitorMencoesSolitudine) {
  const vouchers = arguments[0];
  const eventos = [];
  const recentes = new Map();
  let centralAnterior = null;
  let historicoAnterior = new Map();
  let aberturaSolicitada = false;
  const vistos = new WeakMap();
  const seletor = '.nitro-notification-bubble, .notification-bubble, .nitro-alert, [role="alert"]';
  function emitir(pedido, origem) {
    const agora = Date.now();
    for (const [chave, item] of recentes) {
      if (agora - item.tempo > 15000) recentes.delete(chave);
    }
    const anterior = recentes.get(pedido);
    if (anterior && anterior.origem !== origem) {
      recentes.delete(pedido);
      return;
    }
    recentes.set(pedido, {origem, tempo: agora});
    if (eventos.length < 50) eventos.push(pedido);
  }
  function atualizarCentral() {
    const painel = [...document.querySelectorAll('.nitro-card')].find(p =>
      p.querySelector('.nitro-card-header')?.textContent.includes('Central de Notificações') &&
      p.getClientRects().length);
    if (painel) aberturaSolicitada = true;
    if (!painel && !aberturaSolicitada) {
      const atalho = [...document.querySelectorAll('.navigation-item, #toolbar-friend-bar-container [title], [aria-label="Central de Notificações"]')].find(el =>
        el.getClientRects().length && (/central de notificações/i.test(el.title || el.getAttribute('aria-label') || '') ||
          /(?:^|\s)icon-(?:notification-center|notifications)(?:\s|$)/.test(el.className)));
      if (atalho) {
        aberturaSolicitada = true;
        atalho.click();
      }
    }
    const atual = new Map();
    if (painel) {
      for (const folha of painel.querySelectorAll('div, span, p')) {
        if (folha.childElementCount) continue;
        const m = /^Solitudine mencionou você:\s*(.+?)\s+Clique aqui para ir ao quarto\.$/iu.exec(folha.textContent.trim());
        if (m) {
          // A linha do histórico exibe o remetente em um campo separado.
          let linha = folha.parentElement;
          while (linha && linha !== painel && !linha.innerText.split('\n').some(s => s.trim() === 'Solitudine')) {
            linha = linha.parentElement;
          }
          if (linha && linha !== painel && !linha.querySelector('input, select') &&
              (linha.innerText.match(/mencionou você:/gi) || []).length === 1) {
            atual.set(m[1], (atual.get(m[1]) || 0) + 1);
          }
        }
      }
      if (painel === centralAnterior) {
        for (const [pedido, total] of historicoAnterior) {
          atual.set(pedido, Math.max(total, atual.get(pedido) || 0));
        }
        for (const [pedido, total] of atual) {
          for (let i = historicoAnterior.get(pedido) || 0; i < total; i++) emitir(pedido, 'central');
        }
      }
    }
    centralAnterior = painel;
    historicoAnterior = atual;
  }
  function ler(el) {
    const texto = (el.innerText || '').trim();
    const match = /^(?:Solitudine\s*\n\s*)?Solitudine mencionou você\s*:\s*([^\r\n]+)$/iu.exec(texto);
    const codigo = vouchers && /(?:voucher|código)\s*[:=]\s*([A-Z][A-Z0-9]{3,39})\b/iu.exec(texto);
    const pedido = match ? match[1] : (codigo && /[0-9]/.test(codigo[1]) ? 'voucher ' + codigo[1] : null);
    if (!pedido || vistos.get(el) === texto) return;
    if (/^(?:@\S+\s+)?(?:trade|c[aâ]mbio\s+(?:Duckets|Diamantes)|reciclar(?:\s+\d+)?)(?:\s+Clique aqui para ir ao quarto\.)?$/i.test(pedido.trim())) return;
    vistos.set(el, texto);
    emitir(pedido.replace(/\s+Clique aqui para ir ao quarto\.$/i, ''), 'aviso');
  }
  const observer = new MutationObserver(registros => {
    const avisos = new Set();
    for (const registro of registros) {
      const alvo = registro.target.nodeType === 1 ? registro.target : registro.target.parentElement;
      const pai = alvo?.closest(seletor);
      if (pai) avisos.add(pai);
      for (const no of registro.addedNodes) {
        if (no.nodeType !== 1) continue;
        if (no.matches(seletor)) avisos.add(no);
        for (const el of no.querySelectorAll(seletor)) avisos.add(el);
      }
    }
    avisos.forEach(ler);
  });
  observer.observe(document.body, {childList:true, characterData:true, subtree:true});
  window.monitorMencoesSolitudine = eventos;
  window.atualizarCentralMencoes = atualizarCentral;
}
window.atualizarCentralMencoes();
return window.monitorMencoesSolitudine.splice(0);
