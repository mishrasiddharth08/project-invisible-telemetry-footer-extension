// Offline only: fake readings, a minimal DOM and virtual time; no browser or GPU.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function sample(pct = 20, compact = true) {
  return {
    cfg: {position:'quicksettings', compact, idle_ms:1, busy_ms:1, amber:80, red:95},
    cpu: {name:'Test CPU', pct, mhz_now:4200},
    ram: {pct, used_gb:pct, total_gb:100},
    ram_hw: {vendor:'Test RAM', part:'KIT-123', kind:'DDR5', speed_mhz:5600},
    gpus: [{index:0, name:'Test GPU', pct, used_gb:pct, total_gb:100}],
  };
}

function harness(options = {}) {
  let now = 0, nextId = 0;
  const timers = new Map(), requests = [], events = {};
  const nodes = {};
  for (const k of ['cpu','ram','gpu0']) {
    for (const kind of ['v','s','g','t','w']) {
      nodes['[data-'+kind+'="'+k+'"]'] = {textContent:'', className:'', style:{}};
    }
  }
  nodes['[data-pi-connection]'] = {textContent:'', hidden:true};
  const classes = new Set();
  function element(className = '') {
    const el = {className, style:{}, isConnected:true, attributes:{}, listeners:{}, textContent:'',
      setAttribute(k,v){this.attributes[k]=v;}, addEventListener(k,fn){this.listeners[k]=fn;},
      focus(){doc.activeElement=this;}, select(){},
    };
    const names = new Set(className.split(' '));
    el.classList = {add:k=>names.add(k), remove:k=>names.delete(k), contains:k=>names.has(k),
      toggle:(k,v)=>v ? names.add(k) : names.delete(k)};
    return el;
  }
  let gpuCells = [], copyButton;
  const copyStatus = {textContent:''};
  const strip = {
    style:{}, isConnected:true,
    classList:{add:k=>classes.add(k),remove:k=>classes.delete(k),toggle:(k,v)=>v ? classes.add(k) : classes.delete(k)},
    contains:el=>!!el && (gpuCells.includes(el) || el===copyButton),
    querySelector:s=>s==='.pi-gpu' ? gpuCells[0] : s==='.pi-copy' ? copyButton : s==='.pi-copy-status' ? copyStatus : nodes[s] || null,
    querySelectorAll:s=>s==='.pi-gpu' ? gpuCells : [],
    set innerHTML(html) {
      gpuCells = [...html.matchAll(/class="pi-cell pi-gpu"/g)].map(()=>element('pi-gpu'));
      copyButton = element('pi-copy');
    },
  };
  const body = {children:[],appendChild(el){this.children.push(el);el.parentNode=this;},
    removeChild(el){this.children.splice(this.children.indexOf(el),1);el.parentNode=null;}};
  const doc = {hidden:false, body, createElement:()=>element(),
    execCommand:()=>options.legacyResult ?? false, addEventListener:(k,fn)=>events[k]=fn};
  const context = {
    console, document:doc, navigator:options.navigator || {}, window:{addEventListener:(k,fn)=>events[k]=fn},
    performance:{now:()=>now}, AbortController,
    onUiLoaded:()=>{}, onUiUpdate:()=>{}, MutationObserver:class {observe(){}},
    setTimeout:(fn,ms)=>{ const id=++nextId; timers.set(id,{fn,at:now+ms}); return id; },
    clearTimeout:id=>timers.delete(id),
    fetch:(url, options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject})),
  };
  context.globalThis = context;
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname,'../javascript/telemetry_footer.js'),'utf8');
  // Expose functions only inside this isolated VM; production source is untouched.
  const hook = 'globalThis.api={set(s,el){last=s;strips=[el];shapeKey=shapeOf(s);position=s.cfg.position;started=true;},rebuild(){shapeKey="";paint();},onCopy,onGpuCycle,makeStrip,paint,maybeFetch,interval,buildHtml,state(){return {last,inFlight,connectionLost,failures,localGpuIndex};}};})();';
  assert.match(source, /\}\)\(\);\s*$/);
  vm.runInContext(source.replace(/\}\)\(\);\s*$/,hook),context);
  context.api.set(sample(),strip);
  async function flush() { for(let i=0;i<20;i++) await Promise.resolve(); }
  async function advance(ms) {
    const until=now+ms;
    while(true) {
      const due=[...timers.entries()].filter(([,v])=>v.at<=until).sort((a,b)=>a[1].at-b[1].at)[0];
      if(!due) break;
      now=due[1].at; timers.delete(due[0]); due[1].fn(); await flush();
    }
    now=until;
    await flush();
  }
  return {api:context.api,nodes,strip,doc,events,classes,timers,requests,flush,advance,copyStatus,
    reply:(n,s)=>requests[n].resolve({ok:true,json:()=>Promise.resolve(s)})};
}

test('a sudden memory jump updates number, amount, bar and colour together',()=>{
  const h=harness(); h.api.paint(); h.api.set(sample(90),h.strip); h.api.paint();
  for(const k of ['ram','gpu0']) {
    assert.equal(h.nodes['[data-v="'+k+'"]'].textContent,'90');
    assert.equal(h.nodes['[data-s="'+k+'"]'].textContent,'90.0 / 100.0 GiB');
    assert.equal(h.nodes['[data-g="'+k+'"]'].style.width,'90.0%');
    assert.equal(h.nodes['[data-v="'+k+'"]'].className,'pi-val pi-warn');
  }
  h.api.set(sample(95.2,false),h.strip); h.api.paint();
  assert.equal(h.nodes['[data-v="ram"]'].textContent,'95.2');
  assert.equal(h.nodes['[data-v="ram"]'].className,'pi-val pi-crit');
  assert.equal(h.nodes['[data-s="cpu"]'].textContent,'4.20 GHz');
  h.api.set(sample(20),h.strip); h.api.paint();
  assert.equal(h.nodes['[data-v="ram"]'].className,'pi-val pi-ok');
});

test('unknown GPU memory is neutral and recovers on a valid zero',()=>{
  const h=harness(), s=sample();
  s.gpus[0].pct=null; s.gpus[0].used_gb=null;
  h.api.set(s,h.strip); h.api.paint();
  assert.equal(h.nodes['[data-v="gpu0"]'].textContent,'N/A');
  assert.equal(h.nodes['[data-s="gpu0"]'].textContent,'N/A');
  assert.match(h.nodes['[data-g="gpu0"]'].className,/unavailable/);
  s.gpus[0].pct=0; s.gpus[0].used_gb=0; h.api.paint();
  assert.equal(h.nodes['[data-v="gpu0"]'].textContent,'0');
});

test('legacy 1 ms settings have a floor, and hardware labels are explicit',()=>{
  const h=harness();
  assert.equal(h.api.interval(),250);
  const html=h.api.buildHtml(sample());
  assert.match(html,/>VRAM</);
  assert.match(html,/KIT-123/);
  // Each temperature has a separate labelled row, not a crowded identity header.
  for (const k of ['cpu','ram','gpu0']) {
    assert.match(html,new RegExp('Power consumption · <span data-w="'+k+'" title="Power consumption">N/A W</span></span><span class="pi-temp pi-unavailable" data-t="'+k+'">N/A</span>'));
  }
  const s=sample(); s.cfg.idle_ms=2000; h.api.set(s,h.strip);
  assert.equal(h.api.interval(),2000);
});

test('temperatures repaint with readings and unavailable sensors never become zero',()=>{
  const h=harness(), s=sample();
  s.cpu.temperature_c=65.5; s.ram.temperature_c=42; s.gpus[0].temperature_c=58;
  h.api.set(s,h.strip); h.api.paint();
  assert.equal(h.nodes['[data-t="cpu"]'].textContent,'65.5 °C');
  assert.equal(h.nodes['[data-t="ram"]'].textContent,'42.0 °C');
  assert.equal(h.nodes['[data-t="gpu0"]'].textContent,'58.0 °C');
  for (const k of ['cpu','ram','gpu0']) assert.equal(h.nodes['[data-t="'+k+'"]'].className,'pi-temp pi-ok');
  s.cpu.temperature_c=80; s.ram.temperature_c=55; s.gpus[0].temperature_c=80;
  h.api.paint();
  for (const k of ['cpu','ram','gpu0']) assert.equal(h.nodes['[data-t="'+k+'"]'].className,'pi-temp pi-warn');
  s.cpu.temperature_c=95; s.ram.temperature_c=70; s.gpus[0].temperature_c=90;
  h.api.paint();
  for (const k of ['cpu','ram','gpu0']) assert.equal(h.nodes['[data-t="'+k+'"]'].className,'pi-temp pi-crit');
  s.cfg.temp_cpu_warn=100; s.cfg.temp_cpu_crit=110;
  h.api.paint();
  assert.equal(h.nodes['[data-t="cpu"]'].className,'pi-temp pi-ok');
  s.cpu.temperature_c=70; s.ram.temperature_c=null; s.gpus[0].temperature_c=NaN;
  h.api.set(s,h.strip); h.api.paint();
  assert.equal(h.nodes['[data-t="cpu"]'].textContent,'70.0 °C');
  assert.equal(h.nodes['[data-t="ram"]'].textContent,'N/A');
  assert.equal(h.nodes['[data-t="gpu0"]'].textContent,'N/A');
  assert.equal(h.nodes['[data-t="ram"]'].className,'pi-temp pi-unavailable');
  assert.equal(h.nodes['[data-t="gpu0"]'].className,'pi-temp pi-unavailable');
});

test('power repaints independently of load and never fabricates missing sensors',()=>{
  const h=harness(), s=sample();
  s.cpu.power_w=72.3; s.ram.power_w=0; s.gpus[0].power_w=420.5;
  h.api.set(s,h.strip); h.api.paint();
  assert.equal(h.nodes['[data-w="cpu"]'].textContent,'72.3 W');
  assert.equal(h.nodes['[data-w="ram"]'].textContent,'0.0 W');
  assert.equal(h.nodes['[data-w="gpu0"]'].textContent,'420.5 W');
  s.cpu.power_w=null; s.ram.power_w=-1; s.gpus[0].power_w=NaN;
  h.api.paint();
  for (const k of ['cpu','ram','gpu0']) assert.equal(h.nodes['[data-w="'+k+'"]'].textContent,'N/A W');
});

test('stalled requests time out, retry and ignore a late old reply',async()=>{
  const h=harness(); h.api.maybeFetch(); h.api.maybeFetch(); await h.flush();
  assert.equal(h.requests.length,1);
  await h.advance(5000);
  assert.equal(h.requests[0].options.signal.aborted,true);
  assert.equal(h.api.state().inFlight,false);
  assert.equal(h.api.state().connectionLost,true);
  assert.match(h.nodes['[data-pi-connection]'].textContent,/paused/);
  await h.advance(1000);
  assert.equal(h.requests.length,2);
  h.reply(1,sample(90)); await h.flush();
  assert.equal(h.api.state().last.ram.pct,90);
  assert.equal(h.api.state().connectionLost,false);
  h.reply(0,sample(5)); await h.flush();
  assert.equal(h.api.state().last.ram.pct,90);
});

test('the deadline also covers a stalled JSON body and malformed snapshots',async()=>{
  const h=harness(); h.api.maybeFetch(); await h.flush();
  h.requests[0].resolve({ok:true,json:()=>new Promise(()=>{})}); await h.flush();
  await h.advance(5000);
  assert.equal(h.api.state().connectionLost,true);
  await h.advance(1000); h.reply(1,{error:'temporarily unavailable'}); await h.flush();
  assert.equal(h.api.state().last.ram.pct,20);
  assert.equal(h.api.state().failures,2);
  await h.advance(1999); assert.equal(h.requests.length,2);
  await h.advance(1); assert.equal(h.requests.length,3);
});

test('hidden pages cancel timers and resume without accepting old responses',async()=>{
  const h=harness(); h.api.maybeFetch(); await h.flush();
  h.doc.hidden=true; h.events.visibilitychange();
  assert.equal(h.timers.size,0);
  assert.equal(h.requests[0].options.signal.aborted,true);
  await h.advance(60000); assert.equal(h.requests.length,1);
  h.doc.hidden=false; h.events.visibilitychange(); await h.advance(0);
  assert.equal(h.requests.length,2);
  h.reply(1,sample(80)); await h.flush();
  h.reply(0,sample(2)); await h.flush();
  assert.equal(h.api.state().last.ram.pct,80);
  h.events.pagehide(); assert.equal(h.timers.size,0);
  h.events.pageshow(); await h.advance(0); assert.equal(h.requests.length,3);
});

function multiGpu(h) {
  const s=sample(); s.cfg.gpu_index=0;
  s.gpus.push({...s.gpus[0],index:1,name:'Second GPU'});
  h.api.set(s,h.strip); h.api.rebuild();
  return s;
}
const activation = () => ({stopPropagation(){},preventDefault(){}});
const saved = h => h.requests.filter(r=>r.options.method==='POST');

test('rapid GPU selections serialize saves and ignore snapshots begun before confirmation',async()=>{
  const h=harness(), s=multiGpu(h);
  h.api.onGpuCycle(activation()); await h.flush();
  h.api.onGpuCycle(activation()); h.api.onGpuCycle(activation());
  assert.equal(saved(h).length,1);
  h.api.maybeFetch(); await h.flush();
  saved(h)[0].resolve({ok:true,json:async()=>({ok:true})}); await h.flush();
  assert.equal(saved(h).length,2);
  assert.equal(JSON.parse(saved(h)[1].options.body).gpu_index,0);
  saved(h)[1].resolve({ok:true,json:async()=>({ok:true})}); await h.flush();
  const poll=h.requests.find(r=>r.options.method!=='POST');
  poll.resolve({ok:true,json:async()=>({...s,cfg:{...s.cfg,gpu_index:1}})}); await h.flush();
  assert.equal(h.api.state().localGpuIndex,0);
  h.api.maybeFetch(); await h.flush();
  h.requests.at(-1).resolve({ok:true,json:async()=>s}); await h.flush();
  assert.equal(h.api.state().localGpuIndex,null);
});

test('rejected and stalled GPU saves restore the last confirmed selection',async()=>{
  for (const failure of ['http','json','network','timeout','body']) {
    const h=harness(); multiGpu(h);
    h.api.onGpuCycle(activation()); await h.flush();
    saved(h)[0].resolve({ok:true,json:async()=>({ok:true})}); await h.flush();
    h.api.onGpuCycle(activation()); await h.flush();
    const req=saved(h)[1];
    if(failure==='http') req.resolve({ok:false});
    if(failure==='json') req.resolve({ok:true,json:async()=>({ok:false})});
    if(failure==='network') req.reject(new Error('offline'));
    if(failure==='body') req.resolve({ok:true,json:()=>new Promise(()=>{})});
    await h.flush();
    if(failure==='timeout' || failure==='body') {
      await h.advance(5000); assert.equal(req.options.signal.aborted,true);
      req.resolve({ok:true,json:async()=>({ok:true})}); await h.flush();
    }
    assert.equal(h.api.state().localGpuIndex,1,failure);
  }
});

test('GPU keyboard activation preserves focus and never triggers copy',async()=>{
  const h=harness(); multiGpu(h);
  for(const key of ['Enter',' ']) {
    const gpu=h.strip.querySelector('.pi-gpu'); gpu.focus();
    assert.equal(gpu.attributes.role,'button'); assert.equal(gpu.attributes.tabindex,'0');
    let stopped=false, prevented=false;
    gpu.listeners.keydown({key,stopPropagation(){stopped=true;},preventDefault(){prevented=true;}});
    assert.equal(stopped,true); assert.equal(prevented,true);
    assert.equal(h.doc.activeElement,h.strip.querySelector('.pi-gpu'));
  }
  assert.equal(h.copyStatus.textContent,'');
  const before=h.api.state().localGpuIndex;
  h.strip.querySelector('.pi-gpu').listeners.keydown({key:' ',repeat:true,preventDefault(){}});
  assert.equal(h.api.state().localGpuIndex,before);
  assert.match(h.api.buildHtml(sample()),/<button type="button" class="pi-copy" aria-label="Copy full system snapshot">/);
});

test('copy feedback waits for success and reports fallback failure accurately',async()=>{
  let resolve, reject;
  const h=harness({navigator:{clipboard:{writeText:()=>new Promise((a,b)=>{resolve=a;reject=b;})}}});
  const first=h.api.onCopy({currentTarget:h.strip});
  assert.equal(h.copyStatus.textContent,'Copying…'); assert.equal(h.classes.has('pi-copied'),false);
  resolve(); await first;
  assert.equal(h.copyStatus.textContent,'Copied'); assert.equal(h.classes.has('pi-copied'),true);
  const second=h.api.onCopy({currentTarget:h.strip}); reject(new Error('denied')); await second;
  assert.equal(h.copyStatus.textContent,'Copy failed'); assert.equal(h.classes.has('pi-copied'),false);
  assert.equal(h.doc.body.children.length,0);
  for(const legacyResult of [true,false]) {
    const fallback=harness({legacyResult});
    await fallback.api.onCopy({currentTarget:fallback.strip});
    assert.equal(fallback.copyStatus.textContent,legacyResult?'Copied':'Copy failed');
    assert.equal(fallback.doc.body.children.length,0);
  }
});
