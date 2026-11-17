const modo = arguments[0];
if (window.loopFurni?.modo === modo) return true;
if (window.loopFurni) clearInterval(window.loopFurni.timer);
window.loopFurni = null;
if (modo === 'CANCELAR') return true;
const painel = [...document.querySelectorAll('.nitro-card')].find(p =>
  p.querySelector('input[placeholder="Digite para procurar..."]') &&
  [...p.querySelectorAll('th')].some(th => th.textContent.trim() === 'ID'));
if (!painel) return 'abrir';
const linhas = [...painel.querySelectorAll('tr')].map(tr => ({
  nome: tr.cells[0]?.innerText.trim(), id: tr.cells[2]?.innerText.trim(), tr
})).filter(x => /^\d+$/.test(x.id) && (modo === 'Cookie' ? /cookie/i : /dado|dice/i).test(x.nome));
if (!linhas.length) return false;
const lista = modo === 'Todos os dados' ? linhas : linhas.slice(0,1);
let indice = 0;
let selecionado = false;
let desde = 0;
const loop = {modo, timer:null};
function tick() {
  const item = lista[indice];
  if (!painel.isConnected || !item.tr.isConnected) {
    clearInterval(loop.timer);
    window.loopFurni = null;
    return;
  }
  if (!selecionado) {
    item.tr.cells[0].click();
    selecionado = true;
    desde = performance.now();
    return;
  }
  const botao = [...document.querySelectorAll('.btn, button')].find(e =>
    e.getClientRects().length && e.textContent.trim().toLowerCase() === 'usar');
  let idConfere = false;
  for (let p = botao?.parentElement; p && p !== document.body && p.id !== 'root'; p = p.parentElement) {
    if (p.contains(painel)) break;
    if ([...p.querySelectorAll('[title], [aria-label]')].some(el =>
      [el.title, el.getAttribute('aria-label')].includes('VirtualId: ' + item.id))) {
      idConfere = true;
      break;
    }
  }
  if (!idConfere) {
    if (performance.now()-desde > 1500) selecionado = false;
    return;
  }
  botao.click();
  if (lista.length > 1) {
    indice = (indice+1)%lista.length;
    // Seleciona o próximo imediatamente; o próximo tick confirma seu ID.
    lista[indice].tr.cells[0].click();
    desde = performance.now();
  }
}
window.loopFurni = loop;
tick();
loop.timer = setInterval(tick,100);
return true;
