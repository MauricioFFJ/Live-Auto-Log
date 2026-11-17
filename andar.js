// Consulta a geometria de quarto somente quando o cliente a expõe.
const [modo, alvo, usuario, verificar] = arguments;
const engine = window.Nitro?.instance?.roomEngine;
const sala = engine?.activeRoomId;
const sessao = window.Nitro?.instance?.roomSessionManager?.getSession?.(sala);
if (!sessao || !engine.getRoomObjects) throw Error('Geometria pública do quarto indisponível');
const avatares = engine.getRoomObjects(sala, 100);
const mobis = engine.getRoomObjects(sala, 10);
const pessoa = nome => {
  const dados = sessao.userDataManager.getUserDataByName(nome);
  return dados && avatares.find(a => a.id === dados.roomIndex);
};
const proprio = pessoa(usuario);
if (!proprio) throw Error('Personagem da sessão não localizado');
const eu = proprio.getLocation();
if (verificar) return [Math.round(eu.x), Math.round(eu.y)];
const ref = modo === 'frente' ? pessoa(alvo) : mobis.find(m => String(m.id) === alvo);
if (!ref) throw Error('Alvo ausente no quarto');
const pos = ref.getLocation();
const mapa = engine.getRoomTileObjectMap?.(sala);
const alturas = engine.getRoomGeometry?.(sala)?.getHeight ? engine.getRoomGeometry(sala) : engine.getRoomInstanceData?.(sala)?.tileHeightMap;
if (!mapa || !alturas?.getHeight) throw Error('Mapa de ocupação ou alturas indisponível');
const direcoes = [[0,-1],[1,-1],[1,0],[1,1],[0,1],[-1,1],[-1,0],[-1,-1]];
const candidatos = modo === 'frente' ? [direcoes[(Math.round(ref.getDirection().x / 45) % 8 + 8)%8]] :
  direcoes.filter((_,i) => i%2 === 0);
const livres = candidatos.map(([dx,dy])=>[Math.round(pos.x)+dx,Math.round(pos.y)+dy])
  .filter(([x,y]) => x>=0 && y>=0 && Number.isFinite(alturas.getHeight(x,y)) && alturas.getHeight(x,y)>=0 &&
    typeof mapa.getObjectInTile === 'function' && !mapa.getObjectInTile(x,y) &&
    !avatares.some(a=>a.id!==proprio.id && Math.round(a.getLocation().x)===x && Math.round(a.getLocation().y)===y));
if (!livres.length) throw Error('Nenhuma casa livre identificada junto à referência');
livres.sort((a,b)=>Math.hypot(a[0]-eu.x,a[1]-eu.y)-Math.hypot(b[0]-eu.x,b[1]-eu.y));
const [x,y] = livres[0];
if(x===Math.round(eu.x) && y===Math.round(eu.y)) return {x,y,chegou:true};
const render = engine.getRoomInstanceRenderingCanvas(sala,1);
const geom = render?.geometry;
const centro = engine.getRoomObjectScreenLocation(sala,proprio.id,100,1);
const canvases = [...document.querySelectorAll('canvas')].filter(c=>c.clientWidth>=render.width*.8 && c.getClientRects().length);
if(!centro || !geom || canvases.length!==1) throw Error('Projeção do piso indisponível');
const dest = new eu.constructor(x,y,alturas.getHeight(x,y));
const a = geom.getScreenPoint(eu), b = geom.getScreenPoint(dest);
const canvas = canvases[0], rect=canvas.getBoundingClientRect();
const cx = Math.round((centro.x+b.x-a.x)*rect.width/render.width);
const cy = Math.round((centro.y+b.y-a.y)*rect.height/render.height);
if(document.elementFromPoint(rect.left+cx,rect.top+cy)!==canvas) throw Error('A casa não está visível para clique');
return {canvas,x,y,dx:cx-Math.round(rect.width/2),dy:cy-Math.round(rect.height/2)};
