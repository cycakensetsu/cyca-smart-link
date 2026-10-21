function createEffects(root) {
  'use strict';
  const $ = id => root.querySelector("#" + id);
  const labels = {
    start: ['◎', 'さあ、見積の構想をかたちに。'], upload: ['↗', '原本を受け取りました'],
    read: ['⌕', '読み取り完了。原本と確認しましょう'], check: ['✓', '原本の確認ができました'],
    preview: ['◇', '完成イメージができました'], output: ['✦', '見積書が完成しました！'],
    finish: ['↧', '出力ファイルを用意しました。おつかれさまです']
  };
  // Original synthesized motifs: no sound downloads, tracking, or quote data.
  const motifs = {
    start: [[0,4,7],[0,7,12],[4,7,12],[0,2,7]],
    upload: [[7,12],[4,9],[0,7],[2,9]],
    read: [[0,4,7,12],[0,7,9,12],[4,7,11,12],[0,2,7,9]],
    check: [[0,7,12],[4,7,12],[2,7,14],[0,4,12]],
    preview: [[12,7,14],[7,12,16],[9,12,19],[4,12,14]],
    output: [[0,4,7,12,16],[0,7,12,14,19],[4,7,11,16,19],[0,4,9,12,21]],
    finish: [[12,9,7,12],[7,4,0,12],[16,12,7,12],[14,9,7,12]]
  };
  let context, master, enabled = false, lastVoice = '', lastAnimation = '', pending, timer;
  const seen = new Set(), lastMotif = {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const storage = {get: () => {try{return JSON.parse(localStorage.getItem('cyca_effect_preferences') || '{}');}catch{return {};}},
    set: v => {try{localStorage.setItem('cyca_effect_preferences',JSON.stringify(v));}catch{}}};
  const saved = storage.get();
  if ([0,5,10,15,20,25,30,35,40,45,50].includes(saved.volume)) $('volume').value = saved.volume;
  if (saved.motion === false) $('motion').checked = false;
  if (['shuffle','glass','wood','soft'].includes(saved.theme)) $('theme').value = saved.theme;
  const persist = () => storage.set({volume:Number($('volume').value),motion:$('motion').checked,theme:$('theme').value});
  const choose = (values, previous) => {const available=values.filter(v=>v!==previous);return available[Math.floor(Math.random()*available.length)];};
  const stopSound = () => {if(context && master){master.gain.cancelScheduledValues(context.currentTime);master.gain.setTargetAtTime(0,context.currentTime,.012);}};
  async function unlock() {
    try {
      if (!context) {context = new (window.AudioContext || window.webkitAudioContext)(); master=context.createGain();master.connect(context.destination);}
      if (context.state !== 'running') await context.resume();
      if(context.state !== 'running') throw new Error('suspended');
      enabled=true; updateButton(); return true;
    } catch {enabled=false;updateButton();$('hint').textContent='音を再生できません。消音のまま見積は作れます。';return false;}
  }
  function updateButton(){ $('sound').textContent=enabled?'🔊 音を消す':'音つきで始める';$('sound').setAttribute('aria-pressed',String(enabled));$('hint').textContent=enabled?'短い効果音だけ。いつでも消音できます。':'音は最初にボタンを押すと有効になります。';}
  function play(stage) {
    if(!enabled || !context || context.state!=='running' || document.hidden) return;
    const volume=Number($('volume').value)/100;
    if(!volume) return;
    stopSound();
    const now=context.currentTime+.025;
    master.gain.setValueAtTime(volume*.28,now);
    const variants=motifs[stage], index=choose(variants.map((_,i)=>i),lastMotif[stage]);lastMotif[stage]=index;
    const requested=$('theme').value;
    const voice=requested==='shuffle'?choose(['glass','wood','soft'],lastVoice):requested;lastVoice=voice;
    const base=voice==='soft'?261.63:voice==='wood'?329.63:392;
    variants[index].forEach((note,i)=>{
      const at=now+i*(stage==='output'?.125:.105), duration=voice==='glass'?.28:.2;
      const oscillator=context.createOscillator(), envelope=context.createGain();
      oscillator.type=voice==='wood'?'triangle':'sine';oscillator.frequency.value=base*Math.pow(2,note/12);
      envelope.gain.setValueAtTime(0,at);envelope.gain.linearRampToValueAtTime(.6,at+.012);envelope.gain.exponentialRampToValueAtTime(.001,at+duration);
      oscillator.connect(envelope);envelope.connect(master);oscillator.start(at);oscillator.stop(at+duration+.02);
      oscillator.onended=()=>{oscillator.disconnect();envelope.disconnect();};
    });
  }
  function show(stage, sound=true){
    if(!labels[stage]) return;
    $('icon').textContent=labels[stage][0];$('message').textContent=labels[stage][1];
    $('stage').className='stage';
    if($('motion').checked && !reduced.matches && !document.hidden){
      const animation=choose(['pop','glow','slide'],lastAnimation);lastAnimation=animation;
      requestAnimationFrame(()=>$('stage').classList.add(animation));
      if(stage==='output') for(let i=0;i<14;i++){
        const p=document.createElement('i');p.className='spark';p.style.left=`${15+Math.random()*70}%`;p.style.background=choose(['#71e4de','#6ba9ef','#c1f3ff'],null);p.style.setProperty('--dx',`${Math.random()*100-50}px`);$('card').appendChild(p);p.addEventListener('animationend',()=>p.remove(),{once:true});
      }
    }
    if(sound) play(stage);
  }
  $('sound').onclick=async()=>{if(enabled){enabled=false;stopSound();updateButton();}else if(await unlock())show('start');};
  $('sample').onclick=async()=>{if(await unlock())show('output');};
  $('volume').oninput=()=>{$('volumeValue').textContent=`${$('volume').value}%`;persist();if(!Number($('volume').value))stopSound();};
  $('motion').onchange=()=>{persist();if(!$('motion').checked){$('stage').className='stage';root.querySelectorAll('.spark').forEach(p=>p.remove());}};
  $('theme').onchange=persist;
  $('volumeValue').textContent=`${$('volume').value}%`;
  const visibility=()=>{if(document.hidden){stopSound();clearTimeout(timer);pending=null;}};
  document.addEventListener('visibilitychange',visibility);
  function receive(events){
    let newest;
    for(const item of events || []){if(typeof item.id!=='string' || seen.has(item.id) || !labels[item.stage])continue;seen.add(item.id);if(Date.now()/1000-item.at<12)newest=item;}
    if(seen.size>100){const keep=Array.from(seen).slice(-40);seen.clear();keep.forEach(id=>seen.add(id));}
    if(newest){pending=newest;clearTimeout(timer);timer=setTimeout(()=>{if(pending){show(pending.stage);pending=null;}},120);}
  }
  return {receive, card:$('card'), dispose(){stopSound();clearTimeout(timer);document.removeEventListener('visibilitychange',visibility);if(context)context.close().catch(()=>{});}};
}
