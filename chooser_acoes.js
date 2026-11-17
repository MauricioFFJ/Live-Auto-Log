// Rotinas locais à execução no navegador.
const normal = s => String(s || '').normalize('NFC').replace(/\s+/g,' ').trim().toLowerCase();
const vis = el => el && el.isConnected && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
const pausa = ms => new Promise(r=>setTimeout(r,ms));
const seletor = () => [...document.querySelectorAll('select.form-select')].find(s=>vis(s) && [...s.options].some(o=>o.value==='mascote'));
const quadro = () => seletor()?.closest('.nitro-card');
const campo = () => quadro()?.querySelector('input[placeholder="Digite para procurar..."]');
const linhas = () => [...(quadro()?.querySelectorAll('tr') || [])].filter(tr=>tr.cells[0]?.tagName==='TD');
const nomeLinha = tr => (tr.cells[0].querySelector('.text-truncate') || tr.cells[0]).textContent.trim();
function valor(el,v) {
  if (!el || el.value===v) return;
  const proto=el.tagName==='SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto,'value').set.call(el,v);
  for(const evt of ['input','change']) el.dispatchEvent(new Event(evt,{bubbles:true}));
}
async function ate(fn,ms){
  const fim=Date.now()+ms;
  do { const v=fn(); if(v) return v; await pausa(75); } while(Date.now()<fim);
  return fn();
}
function menu(nome){
  return [...document.querySelectorAll('.nitro-context-menu.visible')].find(m=>vis(m) &&
    normal(m.innerText.split('\n').find(s=>s.trim()))===normal(nome));
}
function opcao(m,regex){
  for(const el of m?.querySelectorAll('.menu-item.list-item') || []){
    const match=normal(el.textContent).match(regex);
    if(match && vis(el)) return {el,n:match[1]===undefined ? null : Number(match[1])};
  }
  return null;
}
function fechar(){ const b=quadro()?.querySelector('.nitro-card-header-close'); if(vis(b)){ b.click(); return true; } return false; }
async function selecionar(linha,nome){
  const tr=linha();
  if(!tr) return null;
  tr.click();
  return ate(()=>menu(nome),1100);
}
