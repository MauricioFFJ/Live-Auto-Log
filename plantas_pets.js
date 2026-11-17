const [id,segundos]=arguments;
const chave='__habblive_tratar_carinhos_v3';
const anterior=window[chave];
if(anterior?.id===id) return {aceite:true,id};
if(anterior?.estado==='executando') return {aceite:false,id:anterior.id};
const job={id,estado:'executando',fase:'chooser',total:0,processados:0,tratados:0,carinhos:0,sem_acao:0,erros:0,fechado:false,nome_atual:'',detalhes:'',seq:0,eventos:[]};
window[chave]=job;
const fim=Date.now()+segundos*1000;
const log=s=>{job.eventos.push({seq:++job.seq,texto:s});if(job.eventos.length>100)job.eventos.shift();};
const tratar=m=>opcao(m,/^tratar$/);
const carinho=m=>opcao(m,/^acarici(?:a|ar)\s*\(\s*(\d+)\s*\)$/);
function painelPlanta(nome){
  for(const el of document.querySelectorAll('button,.btn,[role="button"]')){
    if(!vis(el) || normal(el.textContent)!=='tratar') continue;
    for(let p=el.parentElement;p && p!==document.body;p=p.parentElement){
      if(p.contains(quadro())) break;
      const linhas=p.innerText.split('\n').map(normal);
      if(linhas.includes(normal(nome)) && linhas.includes('bem-estar remanescente')) return el;
    }
  }
  return null;
}
async function processar(item){
  const {nome,tr,ordem}=item;
  const linha=()=>tr.isConnected && nomeLinha(tr)===nome ? tr : linhas().filter(t=>nomeLinha(t)===nome)[ordem];
  const selecionarAlvo=async()=>{
    linha()?.click();
    await pausa(75);
    return ate(()=>painelPlanta(nome) || menu(nome),1100);
  };
  let m=await selecionarAlvo() || await selecionarAlvo();
  if(!m) throw Error('menu do mascote não apareceu');
  const botaoPainel=painelPlanta(nome);
  if(botaoPainel){
    botaoPainel.click();
    if(!await ate(()=>!painelPlanta(nome),1600)) throw Error('Tratar continua disponível após o clique');
    job.tratados++;
    return;
  }
  const botoes=()=>tratar(menu(nome)) || carinho(menu(nome));
  await ate(botoes,300);
  const planta=tratar(menu(nome));
  if(planta){planta.el.click();job.tratados++;return;}
  let acao=carinho(menu(nome));
  if(!acao?.n){job.sem_acao++;return;}
  while(acao?.n>0){
    if(Date.now()>fim) throw Error('prazo atingido');
    const antes=acao.n;
    acao.el.click();
    job.carinhos++;
    const mudou=()=>{const m=menu(nome);return !m || (carinho(m)?.n ?? 0)<antes;};
    await ate(mudou,900);
    if(!menu(nome) || carinho(menu(nome))?.n===antes){
      if(!menu(nome)) m=await selecionar(linha,nome);
      else {linha()?.click();await pausa(90);m=menu(nome)||await selecionar(linha,nome);}
      if(!m) throw Error('menu ausente ao conferir o carinho');
    }
    const leitura=await ate(()=>{const m=menu(nome);if(!m)return null;
      const a=carinho(m);return a && a.n<antes?a:(!a && m.querySelector('.menu-item.list-item')?{n:0}:null);},1300);
    if(!leitura) throw Error('contador de carinhos não diminuiu');
    acao=leitura;
  }
}
(async()=>{
  try{
    const s=await ate(seletor,5500);
    if(!s) throw Error('chooser ausente');
    valor(campo(),'');valor(s,'mascote');
    await pausa(200);
    await ate(()=>linhas().length,650);
    const repetidos=new Map();
    const lista=linhas().map(tr=>{
      const nome=nomeLinha(tr),ordem=repetidos.get(nome)||0;
      repetidos.set(nome,ordem+1);
      return {tr,nome,ordem};
    });
    job.total=lista.length;
    for(const item of lista){
      if(Date.now()>fim) throw Error('prazo da fila atingido');
      job.nome_atual=item.nome;job.fase='selecionando '+item.nome;
      try{await processar(item);}catch(e){job.erros++;log(item.nome+': '+e.message);}
      job.processados++;
    }
    job.fechado=fechar();
    job.estado=job.erros||!job.fechado?'parcial':'concluido';
  }catch(e){job.detalhes=String(e.message||e);job.estado='parcial';log(job.detalhes);}
})();
return {aceite:true,id};
