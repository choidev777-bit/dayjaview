# 실시간 테마 트리맵 코드

## HTML

```html
<section class="theme-treemap-section" aria-label="실시간 테마 인사이트">
  <div class="insight-intro">
    <div>
      <h3>실시간 테마 맵</h3>
      <p>면적이 클수록 상승률이 높아요</p>
    </div>
    <span class="live-label"><i></i>LIVE</span>
  </div>

  <div class="treemap-meta">
    <div class="treemap-legend" aria-label="등락 색상 범례">
      <span><i class="legend-up"></i>상승</span>
      <span><i class="legend-flat"></i>보합</span>
      <span><i class="legend-down"></i>하락</span>
    </div>
    <span>상승률 기준</span>
  </div>

  <div class="treemap-shell">
    <div class="treemap" id="theme-treemap" aria-label="실시간 테마 트리맵"></div>
  </div>

  <div class="insight-note">
    <i>i</i>
    <span>예시 체결로 숫자·색상은 0.25초, 면적·위치는 0.75초마다 갱신됩니다. 노란 테두리는 급부상, 흐린 타일은 약화, 회색 타일은 수신 지연 상태입니다.</span>
  </div>
</section>
```

## CSS

```css
:root {
  --surface:#fff;
  --soft:#f7f8fa;
  --faint:#9299a3;
  --line:#e8ebef;
  --up:#e5484d;
  --down:#2878d0;
  --fast:160ms;
}

*{box-sizing:border-box}
button{font:inherit;cursor:pointer}

.insight-intro{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin:2px 0 13px}
.insight-intro h3{margin:0;font-size:17px;letter-spacing:-.025em}
.insight-intro p{margin:3px 0 0;color:var(--faint);font-size:11.5px}
.live-label{display:inline-flex;align-items:center;gap:6px;flex:0 0 auto;padding:5px 8px;border-radius:999px;background:#fff0f1;color:#b92d36;font-size:10.5px;font-weight:900}
.live-label i{width:6px;height:6px;border-radius:50%;background:var(--up);box-shadow:0 0 0 0 rgba(229,72,77,.4);animation:live-pulse 1.15s ease-out infinite}

@keyframes live-pulse{
  0%{box-shadow:0 0 0 0 rgba(229,72,77,.38)}
  70%{box-shadow:0 0 0 6px rgba(229,72,77,0)}
  100%{box-shadow:0 0 0 0 rgba(229,72,77,0)}
}

.treemap-meta{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 2px 8px;color:var(--faint);font-size:10.5px;font-weight:720}
.treemap-legend{display:flex;align-items:center;gap:8px}
.treemap-legend span{display:inline-flex;align-items:center;gap:4px}
.treemap-legend i{width:6px;height:6px;border-radius:50%}
.legend-up{background:var(--up)}
.legend-flat{background:#7d8592}
.legend-down{background:var(--down)}

.treemap-shell{position:relative;height:480px;overflow:hidden;border:1px solid var(--line);border-radius:20px;background:#e9edf2;box-shadow:inset 0 0 0 3px var(--surface)}
.treemap{position:absolute;inset:3px}
.treemap-tile{position:absolute;display:flex;flex-direction:column;align-items:flex-start;justify-content:flex-end;min-width:0;min-height:0;padding:12px;border:3px solid var(--surface);border-radius:13px;color:#fff;text-align:left;overflow:hidden;isolation:isolate;box-shadow:inset 0 1px 0 rgba(255,255,255,.15);transition:left 680ms cubic-bezier(.16,.84,.24,1),top 680ms cubic-bezier(.16,.84,.24,1),width 680ms cubic-bezier(.16,.84,.24,1),height 680ms cubic-bezier(.16,.84,.24,1),background-color 260ms ease,transform var(--fast),filter 260ms ease,opacity 180ms ease}
.treemap-tile.is-new{opacity:0;transition:opacity 180ms ease}
.treemap-tile::before{content:"";position:absolute;z-index:-1;inset:0;background:linear-gradient(155deg,rgba(255,255,255,.15),transparent 48%,rgba(0,0,0,.09))}
.treemap-tile:hover{z-index:2;transform:scale(.985);filter:brightness(1.05)}
.treemap-tile:focus-visible{z-index:3;outline:3px solid rgba(53,108,249,.4);outline-offset:-3px}

.treemap-tile[data-theme-state="RISING_FAST"]{outline:2px solid #ffd15c;outline-offset:-6px;box-shadow:inset 0 1px 0 rgba(255,255,255,.18),0 0 0 0 rgba(255,196,57,.54);animation:tile-rise 1.2s ease-out 1}
.treemap-tile[data-theme-state="WEAKENING"]{filter:saturate(.42) brightness(.92)}
.treemap-tile[data-theme-state="WEAKENING"]:hover{filter:saturate(.42) brightness(1)}
.treemap-tile[data-theme-state="DELAYED"]{filter:grayscale(.82) saturate(.18) brightness(.84)}
.treemap-tile[data-theme-state="DELAYED"]:hover{filter:grayscale(.82) saturate(.18) brightness(.92)}
.treemap-tile[data-theme-state="CLOSED"]{filter:saturate(.25) brightness(.88)}

@keyframes tile-rise{
  0%{box-shadow:inset 0 1px 0 rgba(255,255,255,.18),0 0 0 0 rgba(255,196,57,.5)}
  70%{box-shadow:inset 0 1px 0 rgba(255,255,255,.18),0 0 0 7px rgba(255,196,57,0)}
  100%{box-shadow:inset 0 1px 0 rgba(255,255,255,.18),0 0 0 0 rgba(255,196,57,0)}
}

.tile-state{position:absolute;top:9px;left:9px;padding:3px 6px;border:1px solid rgba(255,255,255,.34);border-radius:999px;background:rgba(12,18,28,.18);color:rgba(255,255,255,.9);font-size:8.5px;font-weight:900}
.treemap-tile[data-theme-state="ACTIVE"] .tile-state{display:none}
.tile-name{display:block;max-width:100%;font-size:15px;font-weight:950;line-height:1.2;letter-spacing:-.03em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 1px 7px rgba(0,0,0,.18)}
.tile-value{display:block;margin-top:4px;font-size:17px;font-weight:950;line-height:1;font-variant-numeric:tabular-nums;text-shadow:0 1px 7px rgba(0,0,0,.18)}
.treemap-tile[data-size="sm"]{padding:9px}
.treemap-tile[data-size="sm"] .tile-name{font-size:12px}
.treemap-tile[data-size="sm"] .tile-value{font-size:13px}
.treemap-tile[data-size="xs"]{padding:7px;justify-content:center}
.treemap-tile[data-size="xs"] .tile-name{font-size:10.5px}
.treemap-tile[data-size="xs"] .tile-value{display:none}

.insight-note{display:flex;align-items:flex-start;gap:7px;margin:10px 2px 0;color:var(--faint);font-size:10.5px;line-height:1.5}
.insight-note i{flex:0 0 14px;display:grid;place-items:center;width:14px;height:14px;margin-top:1px;border-radius:50%;background:var(--soft);font-style:normal;font-weight:900}

@media (prefers-reduced-motion:reduce){
  .live-label i,.treemap-tile{animation:none;transition:none}
}
```

## JavaScript

```javascript
const themeSeeds = [
  {id:"defense",name:"방산",return:4.9,themeState:"RISING_FAST"},
  {id:"nuclear",name:"원전수출",return:3.8,themeState:"ACTIVE"},
  {id:"robot",name:"산업용 로봇",return:3.1,themeState:"ACTIVE"},
  {id:"ai-chip",name:"AI 반도체",return:2.6,themeState:"ACTIVE"},
  {id:"power",name:"전력설비",return:2.0,themeState:"ACTIVE"},
  {id:"bio",name:"바이오 CDMO",return:1.5,themeState:"WEAKENING"},
  {id:"shipping",name:"해운",return:.8,themeState:"ACTIVE"},
  {id:"battery",name:"2차전지",return:-1.3,themeState:"WEAKENING"},
  {id:"entertainment",name:"엔터",return:-.5,themeState:"DELAYED"}
];

const themes = themeSeeds.map(function(theme,index){
  return Object.assign({rank:index+1},theme,{baseReturn:theme.return,momentum:0});
});

let treemapLayout = new Map();
let treemapLayoutSignature = "";
let liveUpdateCount = 0;

function signedPercent(value){
  const normalized=Math.abs(value)<.05?0:value;
  return (normalized>0?"+":"")+normalized.toFixed(1)+"%";
}

function treemapColor(value){
  if(value>.12){
    const strength=Math.min(1,value/6);
    return "hsl("+(357-strength*2)+" 68% "+(55-strength*14)+"%)";
  }
  if(value<-.12){
    const strength=Math.min(1,Math.abs(value)/4);
    return "hsl("+(211+strength*2)+" 64% "+(57-strength*15)+"%)";
  }
  return "#747d89";
}

function visibleTreemapThemes(){
  const selected=themes.filter(function(theme){
    return theme.return>0;
  }).sort(function(a,b){
    return b.return-a.return||a.id.localeCompare(b.id);
  }).slice(0,12);

  return selected.sort(function(a,b){return a.rank-b.rank});
}

function partition(items,x,y,width,height,result){
  if(items.length===0)return;
  if(items.length===1){
    result.push({theme:items[0].theme,x:x,y:y,width:width,height:height});
    return;
  }

  const total=items.reduce(function(sum,item){return sum+item.weight},0);
  let running=0;
  let bestIndex=1;
  let bestDistance=Infinity;

  for(let index=1;index<items.length;index+=1){
    running+=items[index-1].weight;
    const distance=Math.abs(total/2-running);
    if(distance<bestDistance){
      bestDistance=distance;
      bestIndex=index;
    }
  }

  const first=items.slice(0,bestIndex);
  const second=items.slice(bestIndex);
  const firstWeight=first.reduce(function(sum,item){return sum+item.weight},0);
  const ratio=Math.min(.88,Math.max(.12,firstWeight/total));

  if(width>=height){
    const firstWidth=width*ratio;
    partition(first,x,y,firstWidth,height,result);
    partition(second,x+firstWidth,y,width-firstWidth,height,result);
  }else{
    const firstHeight=height*ratio;
    partition(first,x,y,width,firstHeight,result);
    partition(second,x,y+firstHeight,width,height-firstHeight,result);
  }
}

const themeStateLabels={
  ACTIVE:"",
  RISING_FAST:"급부상",
  WEAKENING:"약화",
  DELAYED:"수신 지연",
  CLOSED:"장 마감"
};

function renderTreemap(forceLayout){
  const map=document.querySelector("#theme-treemap");
  if(!map)return;

  const bounds=map.getBoundingClientRect();
  if(!bounds.width||!bounds.height)return;

  const visibleThemes=visibleTreemapThemes();
  const items=visibleThemes.map(function(theme){
    return {theme:theme,weight:theme.return};
  });
  const visibleIds=new Set(visibleThemes.map(function(theme){return theme.id}));

  map.querySelectorAll(".treemap-tile").forEach(function(tile){
    if(!visibleIds.has(tile.dataset.tileId)){
      tile.remove();
      treemapLayout.delete(tile.dataset.tileId);
    }
  });

  const signature=Math.round(bounds.width)+"x"+Math.round(bounds.height)+"|"+
    visibleThemes.map(function(theme){return theme.id}).join("|");

  if(forceLayout||signature!==treemapLayoutSignature||items.some(function(item){
    return !treemapLayout.has(item.theme.id);
  })){
    const layout=[];
    partition(items,0,0,bounds.width,bounds.height,layout);
    treemapLayout=new Map(layout.map(function(box){return [box.theme.id,box]}));
    treemapLayoutSignature=signature;
  }

  items.forEach(function(item){
    const box=treemapLayout.get(item.theme.id);
    if(!box)return;

    let tile=map.querySelector('[data-tile-id="'+item.theme.id+'"]');
    if(!tile){
      tile=document.createElement("button");
      tile.type="button";
      tile.className="treemap-tile is-new";
      tile.dataset.tileId=item.theme.id;
      tile.innerHTML='<span class="tile-state"></span><span class="tile-name"></span><strong class="tile-value"></strong>';
      tile.addEventListener("click",function(){
        openThemeDetail(tile.dataset.tileId,"insight");
      });
      map.appendChild(tile);
    }

    const area=box.width*box.height;
    tile.dataset.size=area<7200?"xs":area<15500?"sm":"lg";
    tile.dataset.themeState=item.theme.themeState;
    tile.style.left=box.x+"px";
    tile.style.top=box.y+"px";
    tile.style.width=box.width+"px";
    tile.style.height=box.height+"px";
    tile.style.backgroundColor=treemapColor(item.theme.return);

    const primary=signedPercent(item.theme.return);
    const stateLabel=themeStateLabels[item.theme.themeState]||"";
    tile.querySelector(".tile-state").textContent=stateLabel;
    tile.querySelector(".tile-name").textContent=item.theme.name;
    tile.querySelector(".tile-value").textContent=primary;
    tile.setAttribute("aria-label",item.theme.name+", "+primary+(stateLabel?", "+stateLabel:"")+", 상세 보기");

    if(tile.classList.contains("is-new")){
      requestAnimationFrame(function(){tile.classList.remove("is-new")});
    }
  });
}

function updateThemeStates(){
  const liveThemes=themes.filter(function(theme){
    return theme.themeState!=="DELAYED"&&theme.themeState!=="CLOSED";
  });
  const fastest=liveThemes.filter(function(theme){
    return theme.return>0;
  }).sort(function(a,b){
    return b.momentum-a.momentum;
  })[0];

  liveThemes.forEach(function(theme){
    if(fastest&&theme.id===fastest.id&&fastest.momentum>.015){
      theme.themeState="RISING_FAST";
      return;
    }
    theme.themeState=theme.return<=0||theme.momentum<-.08?"WEAKENING":"ACTIVE";
  });
}

function updateLiveThemes(){
  liveUpdateCount+=1;

  themes.forEach(function(theme){
    if(theme.themeState==="DELAYED"||theme.themeState==="CLOSED")return;

    const previousReturn=theme.return;
    const returnPull=(theme.baseReturn-theme.return)*.045;
    theme.return=Math.max(-4.5,Math.min(8.5,
      theme.return+returnPull+(Math.random()-.5)*.42
    ));
    theme.momentum=theme.momentum*.72+(theme.return-previousReturn);
  });

  if(liveUpdateCount%12===0)updateThemeStates();
  renderTreemap(liveUpdateCount%3===0);
}

const treemapObserver="ResizeObserver" in window
  ?new ResizeObserver(function(){renderTreemap(true)})
  :null;

if(treemapObserver){
  treemapObserver.observe(document.querySelector("#theme-treemap"));
}

window.addEventListener("resize",function(){renderTreemap(true)});
requestAnimationFrame(function(){renderTreemap(true)});
setInterval(updateLiveThemes,250);
```
