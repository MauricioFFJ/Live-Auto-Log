// Leitura dos controles visíveis. Todos os cliques ficam no Selenium.
const [op, raiz, nome] = arguments;
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const vis = el => el?.isConnected && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
const folhas = p => [...p.querySelectorAll('*')].filter(e => vis(e) &&
  (!e.children.length || [...e.childNodes].some(n=>n.nodeType===3 && norm(n.textContent)===norm(e.textContent))));
const label = (p, s) => folhas(p).find(e => norm(e.textContent) === s);
const numero = s => /^\d+(?:\.\d{3})*$/.test(norm(s)) ? Number(norm(s).replaceAll('.', '')) : null;
const moedas = ['Pixels', 'Duckets', 'Diamantes'];
function assinatura(el) {
  if (!el) return [];
  const nomes = [el.title, el.getAttribute('aria-label'), el.alt, el.src, el.className, getComputedStyle(el).backgroundImage];
  const tags = nomes.filter(x => typeof x === 'string').flatMap(x => x.toLowerCase().match(/(?:currency|moeda)[-_./](?:icon[-_./])?\d+|(?:duckets?|diamantes?|diamonds?|pixels?)(?![a-z])/g) || []);
  const estilo=getComputedStyle(el), b=el.getBoundingClientRect();
  if(b.width>0 && b.width<=26 && b.height<=26) {
    if(el.tagName==='IMG' && el.src) tags.push(el.src);
    if(estilo.backgroundImage.startsWith('url(')) tags.push(estilo.backgroundImage+' '+estilo.backgroundPosition);
  }
  return tags;
}
function marcas(p) {
  return [...new Set([p,...p.querySelectorAll('*')].flatMap(assinatura))];
}
// A barra superior exibe, nessa ordem, Pixels, Duckets e Diamantes.
// Só reconhecer o conjunto de três saldos alinhados no alto à direita.
function lerCarteiras() {
const topo = [...document.querySelectorAll('div, span')].filter(e => {
  if (numero(e.textContent) === null || !vis(e) || ![...e.childNodes].some(n=>n.nodeType===3 && norm(n.textContent)===norm(e.textContent))) return false;
  const b=e.getBoundingClientRect();
  return b.left>innerWidth*.60 && b.top>=0 && b.top<90;
}).sort((a,b)=>a.getBoundingClientRect().top-b.getBoundingClientRect().top);
const primeira = topo[0]?.getBoundingClientRect().top;
const linha = topo.filter(e=>Math.abs(e.getBoundingClientRect().top-primeira)<5)
  .sort((a,b)=>a.getBoundingClientRect().left-b.getBoundingClientRect().left);
function placa(el) {
  for(let p=el.parentElement;p && p!==document.body;p=p.parentElement) {
    const w=p.getBoundingClientRect().width;
    if(w>120) break;
    if(marcas(p).length) return marcas(p);
  }
  return [];
}
return linha.length===3 ? linha.map((e,i)=>({nome:moedas[i], saldo:numero(e.textContent), marcas:placa(e)})) : [];
}
const carteiras = ['carteiras','preco'].includes(op) ? lerCarteiras() : [];
if(op==='carteiras') return carteiras;
if(op==='vip') {
  const linhas = (document.body.innerText || '').split('\n').map(norm);
  if(linhas.includes('Assine!')) return false;
  return linhas.some(s=>/^\d+\s+dia(?:s)?\.?$/i.test(s));
}
if(op==='janela') {
  return [...document.querySelectorAll('.nitro-card,[role="dialog"]')].find(p=>vis(p) &&
    norm(p.querySelector('.nitro-card-header-text,.nitro-card-header')?.textContent)===nome)
    || [...document.querySelectorAll('.nitro-card,[role="dialog"]')].find(p=>vis(p) && norm(p.innerText.split('\n')[0])===nome) || null;
}
if(op==='avisoAtualizacao') {
  for(const aviso of document.querySelectorAll('.nitro-alert,.nitro-card,[role="alert"],[role="dialog"]')){
    if(!vis(aviso) || aviso.matches('.nitro-catalog') || aviso.querySelector('.layout-grid-item,.grid-item')) continue;
    if(!/(?:catálogo|loja).{0,100}(?:atualiza|atualizo)/is.test(aviso.innerText)) continue;
    const botao=[...aviso.querySelectorAll('button,.btn,[role="button"]')].find(e=>vis(e) && /^(?:ok|fechar|entendi|aceitar)$/i.test(norm(e.textContent)));
    if(botao) return botao;
  }
  return null;
}
if(!raiz) return null;
if(op==='rotulo') return label(raiz,nome) || null;
if(op==='opcoes') return [...raiz.querySelectorAll('.layout-grid-item,.grid-item,.catalog-item,[data-offer-id]')].filter(e=>
  vis(e) && !e.parentElement.closest('.layout-grid-item,.grid-item,.catalog-item,[data-offer-id]'));
if(op==='paginaLimitada') return raiz.innerText.split('\n').slice(0,3).some(s=>norm(s)==='Edição Limitada');
if(op==='raros'){
  const sel='.layout-grid-item,.grid-item,.catalog-item,[data-offer-id]';
  return [...raiz.querySelectorAll(sel)].filter(e=>vis(e) && !e.parentElement.closest(sel)).map(e=>{
    const img=e.querySelector('img');
    const nome=norm(e.title || e.getAttribute('aria-label') || img?.alt);
    const arte=[e,...e.querySelectorAll('[style]')].map(n=>getComputedStyle(n).backgroundImage).find(s=>s.startsWith('url('));
    return {el:e,id:e.dataset.offerId || nome || img?.getAttribute('src') || arte || '',nome,
      vip:/\bVIP\b/i.test([nome,e.innerText,...[...e.querySelectorAll('[title]')].map(x=>x.title)].join(' '))};
  });
}
if(op==='estadoRaro'){
  const sel='.layout-grid-item,.grid-item,.catalog-item,[data-offer-id]';
  const es=folhas(raiz).filter(e=>!e.closest(sel));
  if(es.some(e=>norm(e.textContent).toUpperCase()==='ESGOTADO')) return 'esgotado';
  return es.some(e=>norm(e.textContent)==='Comprar') ? 'disponivel' : null;
}
if(op==='busca') return raiz.querySelector('input[placeholder*="Digite para procurar"]');
if(op==='titulo') {
  const titulo=raiz.querySelector('.nitro-catalog-purchase-widget .fw-bold,.nitro-catalog-layout-default .fw-bold,[class*="product-name"],[class*="offer-name"]');
  return titulo && norm(titulo.textContent);
}
if(op==='preco') {
  const preco=label(raiz,'Preço:');
  const comprar=label(raiz,'Comprar');
  if(!preco && !comprar) return null;
  for(let p=(preco || comprar).parentElement; p && p!==raiz; p=p.parentElement) {
    const valores=folhas(p).map(e=>numero(e.textContent)).filter(x=>x!==null);
    if(valores.length!==1) continue;
    const ids=marcas(p);
    const tipos=carteiras.filter(c=>ids.some(x=>c.marcas.includes(x) || x===c.nome.toLowerCase()));
    if(tipos.length===1) return {valor:valores[0], moeda:tipos[0].nome};
  }
}
return null;
