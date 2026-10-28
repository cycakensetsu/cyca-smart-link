function createEffects(root) {
  'use strict';
  const $ = id => root.querySelector("#" + id);
  const labels = {
    start: ['◎', 'さあ、見積づくりをはじめましょう。'], upload: ['↗', '受け取りました。さあ、見積づくり！'],
    read: ['⌕', '読み取りできました。原本と見比べましょう'], check: ['✓', '確認できました。次は金額を決めましょう'],
    preview: ['◇', '完成イメージができました。仕上がりをチェック！'], output: ['✦', 'できました！PDFを保存しましょう'],
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
  let context, master, enabled = true, unlocked = false, unlocking, lastVoice = '', lastAnimation = '', pending, timer, motionTimer, noticeTimer, notice, soundRun = 0, animationRun = 0, currentStage = '', disposed = false;
  const seen = new Set(), lastMotif = {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const storage = {get: () => {try{return JSON.parse(localStorage.getItem('cyca_effect_preferences') || '{}');}catch{return {};}},
    set: v => {try{localStorage.setItem('cyca_effect_preferences',JSON.stringify(v));}catch{}}};
  const saved = storage.get();
  enabled = saved.sound !== false;
  if ([0,5,10,15,20,25,30,35,40,45,50].includes(saved.volume)) $('volume').value = saved.volume;
  if (saved.motion === false) $('motion').checked = false;
  if (['shuffle','spark','glass','wood','soft'].includes(saved.theme)) $('theme').value = saved.theme;
  const persist = () => storage.set({sound:enabled,volume:Number($('volume').value),motion:$('motion').checked,theme:$('theme').value});
  const choose = (values, previous) => {const available=values.filter(v=>v!==previous);return available[Math.floor(Math.random()*available.length)];};
  const stopSound = () => {soundRun++;if(context && master){master.gain.cancelScheduledValues(context.currentTime);master.gain.setTargetAtTime(0,context.currentTime,.012);}};
  async function unlock() {
    try {
      if (!context) {context = new (window.AudioContext || window.webkitAudioContext)(); master=context.createGain();master.connect(context.destination);context.onstatechange=()=>{if(!disposed){unlocked=context.state==='running';updateButton();}};}
      if (context.state !== 'running') await context.resume();
      if(context.state !== 'running') throw new Error('suspended');
      unlocked=true; updateButton(); return true;
    } catch {unlocked=false;updateButton();$('hint').textContent='音を再生できません。消音のまま見積は作れます。';return false;}
  }
  function updateButton(){
    unlocked=!!context && context.state==='running';
    $('sound').textContent=enabled?'🔊 音 ON':'🔇 音 OFF';
    $('sound').setAttribute('aria-pressed',String(enabled));
    $('card').setAttribute('data-sound-state',!enabled?'muted':unlocked?'running':'ready');
    $('hint').textContent=!enabled?'効果音を消しています。':unlocked?'短い音で工程をお知らせします。':'ファイル選択など、最初の操作で音が有効になります。';
  }
  function play(stage) {
    $('card').setAttribute('data-sound-completed','');
    if(!enabled || !context || context.state!=='running'){updateButton();return;}
    const volume=Number($('volume').value)/100;
    if(!volume){if($('audioCheck'))$('audioCheck').textContent='音量は0%です。音を試す場合は、ここの音量を上げてください。';return;}
    stopSound();
    const run=soundRun;
    const now=context.currentTime+.025;
    master.gain.setValueAtTime(volume*.28,now);
    const variants=motifs[stage], index=choose(variants.map((_,i)=>i),lastMotif[stage]);lastMotif[stage]=index;
    const requested=$('theme').value;
    const voice=requested==='shuffle'?choose(['glass','wood','soft','spark'],lastVoice):requested;lastVoice=voice;
    const base=voice==='spark'?440:voice==='soft'?261.63:voice==='wood'?329.63:392;
    let finished=0;
    variants[index].forEach((note,i)=>{
      const at=now+i*(voice==='spark'?.095:stage==='output'?.125:.105), duration=voice==='glass'?.28:.2;
      const oscillator=context.createOscillator(), envelope=context.createGain();
      oscillator.type=voice==='wood'?'triangle':'sine';oscillator.frequency.value=base*Math.pow(2,note/12);
      envelope.gain.setValueAtTime(0,at);envelope.gain.linearRampToValueAtTime(.6,at+.012);envelope.gain.exponentialRampToValueAtTime(.001,at+duration);
      oscillator.connect(envelope);envelope.connect(master);oscillator.start(at);oscillator.stop(at+duration+.02);
      oscillator.onended=()=>{oscillator.disconnect();envelope.disconnect();finished++;if(!disposed && run===soundRun && finished===variants[index].length){$('card').setAttribute('data-sound-completed',stage);$('card').setAttribute('data-sound-ended-at',String(context.currentTime));if($('audioCheck'))$('audioCheck').textContent='短い音を再生しました。聞こえないときは、Macやブラウザの音量をご確認ください。';}};
    });
    $('card').setAttribute('data-last-sound',stage);
  }
  function dismissNotice(){clearTimeout(noticeTimer);if(notice){notice.remove();notice=null;}}
  function stopNoticeMotion(){for(const animation of notice?.getAnimations?.() || [])animation.cancel();}
  function showNotice(stage){
    dismissNotice();
    // v2 shares the app document, so completion remains visible beside the
    // active controls even when the status card is above the viewport.
    if(root===owner || !owner.body || owner.hidden || !['upload','read','output','finish'].includes(stage))return;
    notice=owner.createElement('div');notice.setAttribute('data-cyca-work-notice',stage);notice.setAttribute('aria-hidden','true');
    notice.style.cssText='position:fixed;right:20px;bottom:22px;z-index:10000;pointer-events:none;max-width:min(400px,calc(100vw - 40px));padding:15px 20px;border:1px solid #7bded2;border-radius:17px;background:linear-gradient(125deg,#112f45,#225865);box-shadow:0 10px 32px #09283b33;color:#fff1d7;font:650 14px/1.65 -apple-system,BlinkMacSystemFont,sans-serif;';
    notice.textContent=(stage==='output'?'✦ ':stage==='read'?'✨ ':stage==='upload'?'📄 ':'🎁 ')+labels[stage][1];owner.body.appendChild(notice);
    if($('motion').checked && !reduced.matches)notice.animate?.([{opacity:0,transform:'translateY(12px) scale(.97)'},{opacity:1,transform:'translateY(0) scale(1)'}],{duration:320,easing:'ease-out'});
    noticeTimer=setTimeout(dismissNotice,3200);
  }
  function syncPageMotion(){
    const paused=!$('motion').checked || reduced.matches || owner.hidden;
    owner.documentElement?.style?.setProperty('--cyca-motion-play-state',paused?'paused':'running');
    owner.documentElement?.style?.setProperty('--cyca-sweep-opacity',paused?'0':'1');
  }
  function stopMotion(state){
    animationRun++;clearTimeout(motionTimer);
    $('stage').className='stage'+(currentStage?' phase-'+currentStage:'');
    root.querySelectorAll('.spark').forEach(p=>p.remove());
    $('card').setAttribute('data-motion-state',state);
  }
  function show(stage, sound=true){
    if(disposed || !labels[stage]) return;
    $('icon').textContent=labels[stage][0];$('message').textContent=labels[stage][1];
    currentStage=stage;
    stopMotion('idle');
    $('stage').className='stage phase-'+stage;
    $('card').setAttribute('data-stage',stage);
    showNotice(stage);
    if($('motion').checked && !reduced.matches && !document.hidden){
      const animation=choose(['motion-orbit','motion-trace','motion-sweep'],lastAnimation);lastAnimation=animation;
      const run=animationRun;
      $('card').setAttribute('data-last-animation',animation);
      $('card').setAttribute('data-motion-state','running');
      requestAnimationFrame(()=>{
        if(disposed || run!==animationRun || !$('motion').checked || reduced.matches || document.hidden)return;
        $('stage').classList.add('is-animated',animation);
      });
      if(stage==='output') for(let i=0;i<12;i++){
        const p=document.createElement('i');p.className='spark';p.style.left=`${15+Math.random()*70}%`;p.style.background=choose(['#71e4de','#6ba9ef','#ffd38a','#d5c8ff'],null);p.style.setProperty('--dx',`${Math.random()*100-50}px`);$('card').appendChild(p);p.addEventListener('animationend',()=>p.remove(),{once:true});
      }
      motionTimer=setTimeout(()=>stopMotion('idle'),1700);
    }else $('card').setAttribute('data-motion-state',reduced.matches?'reduced':document.hidden?'hidden':'off');
    if(sound) play(stage);
  }
  $('sound').onclick=async()=>{
    if(enabled){enabled=false;stopSound();}
    else {enabled=true;if(await unlock())show('start');}
    persist();updateButton();
  };
  $('sample').onclick=async()=>{enabled=true;persist();if(await unlock())show('output');};
  $('volume').oninput=()=>{$('volumeValue').textContent=`${$('volume').value}%`;persist();if(!Number($('volume').value))stopSound();};
  $('motion').onchange=()=>{persist();syncPageMotion();if(!$('motion').checked){stopMotion('off');stopNoticeMotion();}};
  $('theme').onchange=persist;
  $('volumeValue').textContent=`${$('volume').value}%`;
  // v2 lives in the application's document (including a shadow root). Unlock
  // synchronously from the ordinary upload/click/key gesture, not a hidden
  // settings button. Safari requires resume() to originate in this gesture.
  const owner=root.ownerDocument || document;
  const gesture=event=>{
    if(!enabled || (unlocked && context?.state==='running') || unlocking || event?.isTrusted===false)return;
    const path=event?.composedPath?.() || [];
    if(path.includes($('sound')) || path.includes($('sample')))return;
    unlocking=unlock().then(ok=>{if(ok)show('start');}).finally(()=>{unlocking=null;});
    return unlocking;
  };
  owner.addEventListener('pointerdown',gesture,true);
  owner.addEventListener('keydown',gesture,true);
  const quietMotion=()=>{syncPageMotion();if(owner.hidden)dismissNotice();if(reduced.matches || owner.hidden){stopMotion(reduced.matches?'reduced':'hidden');stopNoticeMotion();}};
  owner.addEventListener('visibilitychange',quietMotion);
  reduced.addEventListener?.('change',quietMotion);
  syncPageMotion();
  updateButton();
  function receive(events){
    if(disposed)return;
    let newest;
    for(const item of events || []){if(typeof item.id!=='string' || seen.has(item.id) || !labels[item.stage])continue;seen.add(item.id);if(Date.now()/1000-item.at<12)newest=item;}
    if(seen.size>100){const keep=Array.from(seen).slice(-40);seen.clear();keep.forEach(id=>seen.add(id));}
    if(newest){pending=newest;clearTimeout(timer);timer=setTimeout(()=>{if(pending){show(pending.stage);pending=null;}},120);}
  }
  return {receive, card:$('card'), dispose(){disposed=true;stopSound();stopMotion('disposed');dismissNotice();clearTimeout(timer);owner.removeEventListener('visibilitychange',quietMotion);reduced.removeEventListener?.('change',quietMotion);owner.removeEventListener('pointerdown',gesture,true);owner.removeEventListener('keydown',gesture,true);if(context)context.close().catch(()=>{});}};
}
