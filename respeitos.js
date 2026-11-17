const [nome,limite]=arguments;
const fim=Date.now()+limite, callback=arguments[arguments.length-1];
const resultado={fase:'chooser',trilha:[],respeitosAntes:0,respeitosDepois:0};
const linha=()=>linhas().find(tr=>normal(nomeLinha(tr))===normal(nome));
const respeito=m=>opcao(m,/^respeitar\s*\(\s*(\d+)\s*\)$/);
const mais=m=>opcao(m,/^mais respeitos?$/);
async function estado(anterior,comprado){
  const pronto=()=>{
    const m=menu(nome), r=respeito(m), extra=mais(m);
    if(r && r.n>0 && (anterior==null || r.n<anterior)) return r;
    if(!comprado && extra && !(r?.n>0)) return {n:0};
    return null;
  };
  for(let i=0;i<4 && Date.now()<fim-1000;i++){
    const atual=pronto(); if(atual) return atual;
    if(!menu(nome)) await selecionar(linha,nome);
    const novo=await ate(pronto,i===0?800:1350);
    if(novo) return novo;
    // Alguns clientes só atualizam o contador ao reabrir o menu.
    const tr=linha(); if(tr && menu(nome)) { tr.click(); await pausa(100); }
  }
  const mesmo=respeito(menu(nome));
  if(anterior!==null && mesmo?.n===anterior) return mesmo;
  throw Error('Sem atualização do contador para '+nome);
}
async function gastar(chave,aposCompra){
  let anterior=null, tentativas=0;
  while(Date.now()<fim-1000){
    const atual=await estado(anterior,aposCompra && anterior===null);
    if(anterior!==null && atual.n<anterior){
      resultado[chave]+=anterior-atual.n;
      anterior=atual.n;
      tentativas=0;
    }
    if(atual.n===0) return;
    if(tentativas>=3) throw Error('Contador parado em '+atual.n+' para '+nome);
    // O menu acompanha o avatar. Localize novamente a ação no DOM antes do clique.
    const botao=respeito(menu(nome));
    if(!botao || botao.n!==atual.n) continue;
    anterior=atual.n;
    botao.el.click();
    tentativas++;
    await pausa(100);
  }
  throw Error('Prazo atingido durante o lote');
}
(async()=>{
  try{
    if(!await ate(seletor,5500)) throw Error('Chooser ausente');
    valor(seletor(),'live'); valor(campo(),nome);
    if(!await ate(linha,2200)){fechar();resultado.estado='usuario_ausente';return;}
    resultado.fase='primeiro lote';
    await gastar('respeitosAntes',false);
    resultado.fase='compra única';
    const compra=mais(menu(nome));
    if(!compra) throw Error('Mais respeito indisponível');
    const botoesOk=()=>[...document.querySelectorAll('.btn,button')].filter(e=>vis(e)&&normal(e.textContent)==='ok');
    const existentes=new Set(botoesOk());
    compra.el.click();
    const ok=await ate(()=>{const novos=botoesOk().filter(b=>!existentes.has(b));return novos.length===1 && novos[0];},4500);
    if(!ok) throw Error('Confirmação da compra ausente');
    ok.click();
    resultado.fase='lote comprado';
    await gastar('respeitosDepois',true);
    resultado.estado=fechar()?'concluido':'concluido_sem_fechar';
  }catch(e){resultado.estado='incompleto';resultado.erro=String(e.message||e);}
  finally{callback(resultado);}
})();
