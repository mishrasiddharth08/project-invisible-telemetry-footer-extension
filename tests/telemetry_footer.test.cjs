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

function harness() {
  let now = 0, nextId = 0;
  const timers = new Map(), requests = [], events = {};
  const nodes = {};
  for (const k of ['cpu','ram','gpu0']) {
    for (const kind of ['v','s','g','t']) {
      nodes['[data-'+kind+'="'+k+'"]'] = {textContent:'', className:'', style:{}};
    }
  }
  nodes['[data-pi-connection]'] = {textContent:'', hidden:true};
  const classes = new Set();
  const strip = {
    style:{}, isConnected:true,
    classList:{toggle:(k,v)=>v ? classes.add(k) : classes.delete(k)},
    querySelector:s=>nodes[s] || null, querySelectorAll:()=>[],
  };
  const doc = {hidden:false, addEventListener:(k,fn)=>events[k]=fn};
  const context = {
    console, document:doc, window:{addEventListener:(k,fn)=>events[k]=fn},
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
  const hook = 'globalThis.api={set(s,el){last=s;strips=[el];shapeKey=shapeOf(s);position=s.cfg.position;started=true;},paint,maybeFetch,interval,buildHtml,state(){return {last,inFlight,connectionLost,failures};}};})();';
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
  return {api:context.api,nodes,strip,doc,events,classes,timers,requests,flush,advance,
    reply:(n,s)=>requests[n].resolve({ok:true,json:()=>Promise.resolve(s)})};
}

test('a sudden memory jump updates number, amount, bar and colour together',()=>{
  const h=harness(); h.api.paint(); h.api.set(sample(90),h.strip); h.api.paint();
  for(const k of ['ram','gpu0']) {
    assert.equal(h.nodes['[data-v="'+k+'"]'].textContent,'90');
    assert.equal(h.nodes['[data-s="'+k+'"]'].textContent,'90.0 / 100.0 GB');
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

test('legacy 1 ms settings have a floor, and hardware labels are explicit',()=>{
  const h=harness();
  assert.equal(h.api.interval(),250);
  const html=h.api.buildHtml(sample());
  assert.match(html,/>VRAM</);
  assert.match(html,/KIT-123/);
  // Each temperature has a separate labelled row, not a crowded identity header.
  for (const k of ['cpu','ram','gpu0']) {
    const label=k==='gpu0'?'GPU':k.toUpperCase();
    assert.match(html,new RegExp('<div class="pi-thermal"><span class="pi-thermal-label">'+label+' TEMP</span><span class="pi-temp pi-unavailable" data-t="'+k+'">N/A</span></div>'));
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
