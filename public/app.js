const $ = id => document.getElementById(id);
const state = { file: null, activePage: 1, apiKey: '', voiceUrl: null, voiceId: null, selectedTool: 'subtitle', jobPoll: null, positions: {} };
function saveProject() {
  try {
    localStorage.setItem('speedRecapProject', JSON.stringify({
      file: state.file, transcript: $('transcript').value, script: $('script').value,
      aspect: $('aspect').value, mirror: $('mirror').value, voiceId: state.voiceId,
      voiceUrl: state.voiceUrl,
      overlays: ['subtitleOverlay','textOverlay','logoOverlay','blurOverlay'].map(id => {
        const e = $(id); return { id, text: e.textContent, left: e.style.left, top: e.style.top, width: e.style.width, height: e.style.height, fontSize: e.style.fontSize, hidden: e.classList.contains('hidden') };
      })
    }));
  } catch {}
}
function restoreProject() {
  try {
    const p = JSON.parse(localStorage.getItem('speedRecapProject') || 'null');
    if (!p) return;
    $('transcript').value = p.transcript || ''; $('script').value = p.script || '';
    if (p.aspect) { $('aspect').value = p.aspect; $('aspect').dispatchEvent(new Event('change')); }
    if (p.mirror) { $('mirror').value = p.mirror; $('mirror').dispatchEvent(new Event('change')); }
    if (p.file?.id) {
      state.file = p.file; $('toScript').disabled = false;
      $('fileMeta').classList.remove('hidden');
      $('fileMeta').innerHTML = '<b>✓ Saved video</b><br>' + escapeHtml(p.file.name) + ' · ' + fmtBytes(p.file.size) + ' · ' + Math.round(p.file.duration || 0) + ' sec';
      $('previewVideo').src = '/api/source/' + encodeURIComponent(p.file.id);
      $('exportSummary').querySelector('b').textContent = p.file.name;
    }
    state.voiceId = p.voiceId || null; state.voiceUrl = p.voiceUrl || null;
    if (state.voiceUrl) { $('voicePlayer').src=state.voiceUrl; $('voicePlayer').classList.remove('hidden'); $('voiceDownload').href=state.voiceUrl; $('voiceDownload').classList.remove('hidden'); }
    for (const o of p.overlays || []) { const e=$(o.id); if(!e)continue; e.textContent=o.text||e.textContent; for(const k of ['left','top','width','height','fontSize']) if(o[k])e.style[k]=o[k]; e.classList.toggle('hidden',!!o.hidden); }
  } catch {}
}
const pages = [...document.querySelectorAll('.page')];
const stepButtons = [...document.querySelectorAll('.step')];
const toast = (message, error = false) => { $('toast').textContent = message; $('toast').classList.toggle('error', error); $('toast').classList.remove('hidden'); clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').classList.add('hidden'), 4500); };
const showPage = n => { state.activePage = n; pages.forEach(p => p.classList.toggle('active', Number(p.dataset.panel) === n)); stepButtons.forEach(b => b.classList.toggle('active', Number(b.dataset.page) === n)); window.scrollTo({ top: 0, behavior: 'smooth' }); };
const api = async (url, options = {}) => { const headers = { ...(options.headers || {}) }; if (state.apiKey) headers['x-gemini-api-key'] = state.apiKey; const r = await fetch(url, { ...options, headers }); const data = await r.json().catch(() => ({})); if (!r.ok) throw new Error(data.error || 'Request failed (' + r.status + ')'); return data; };
$('settingsToggle').addEventListener('click', () => $('apiPanel').classList.toggle('hidden'));
$('apiKey').addEventListener('input', e => state.apiKey = e.target.value.trim());
stepButtons.forEach(b => b.addEventListener('click', () => { const n = Number(b.dataset.page); if (n > 1 && !state.file) return toast('အရင် video upload လုပ်ပါ။', true); showPage(n); }));
document.querySelectorAll('.back').forEach(b => b.addEventListener('click', () => showPage(Number(b.dataset.back))));
$('videoFile').addEventListener('change', e => { if (e.target.files[0]) uploadVideo(e.target.files[0]); });
const drop = $('dropZone');
drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('dragover'); });
drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('dragover'); const f = e.dataTransfer.files[0]; if (f) uploadVideo(f); });
function fmtBytes(n) { if (n < 1024**2) return (n/1024).toFixed(1)+' KB'; if(n<1024**3) return (n/1024**2).toFixed(1)+' MB'; return (n/1024**3).toFixed(2)+' GB'; }
function uploadVideo(file) {
  if (file.size > 900 * 1024 ** 2) return toast('Video သည် 900 MB ထက်ကြီးနေပါတယ်။', true);
  $('uploadProgressWrap').classList.remove('hidden'); $('uploadLabel').textContent = 'Uploading ' + file.name; $('uploadPct').textContent = '0%'; $('uploadProgress').style.width = '0%'; $('toScript').disabled = true;
  const form = new FormData(); form.append('video', file);
  const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/upload');
  if (state.apiKey) xhr.setRequestHeader('x-gemini-api-key', state.apiKey);
  xhr.upload.onprogress = e => { if (e.lengthComputable) { const p = Math.round(e.loaded/e.total*100); $('uploadPct').textContent = p+'%'; $('uploadProgress').style.width = p+'%'; } };
  xhr.onload = () => { let data = {}; try { data = JSON.parse(xhr.responseText); } catch {} if(xhr.status<200||xhr.status>=300) { toast(data.error||'Upload failed',true); $('uploadLabel').textContent='Upload failed'; return; } state.file = data.file; saveProject(); $('oneClick').disabled=false; $('uploadPct').textContent='100%'; $('uploadProgress').style.width='100%'; $('uploadLabel').textContent='Upload complete'; $('fileMeta').classList.remove('hidden'); $('fileMeta').innerHTML='<b>✓ Video ready</b><br>'+escapeHtml(state.file.name)+' · '+fmtBytes(state.file.size)+' · '+(state.file.width||'?')+'×'+(state.file.height||'?')+' · '+Math.round(state.file.duration)+' sec'; $('toScript').disabled=false; $('previewVideo').src=URL.createObjectURL(file); $('exportSummary').querySelector('b').textContent=state.file.name; toast('Video upload အောင်မြင်ပါပြီ။'); };
  xhr.onerror = () => { toast('Network error ဖြစ်နေပါတယ်။ ထပ်ကြိုးစားပါ။',true); $('uploadLabel').textContent='Upload failed'; }; xhr.send(form);
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function waitForJob(id, onUpdate) {
  while (true) {
    await new Promise(resolve => setTimeout(resolve, 2500));
    const job = await api('/api/jobs/' + id);
    if (onUpdate) onUpdate(job);
    if (job.state === 'done') return job;
    if (job.state === 'failed') throw new Error(job.message || 'Job failed');
  }
}
function buildExportSettings() {
  const preview=$('preview');
  const overlayData={};
  for(const kind of ['subtitle','text','logo','blur']) {
    const el=$(({subtitle:'subtitleOverlay',text:'textOverlay',logo:'logoOverlay',blur:'blurOverlay'})[kind]);
    overlayData[kind]={x:el.offsetLeft/preview.clientWidth,y:el.offsetTop/preview.clientHeight,w:el.offsetWidth/preview.clientWidth,h:el.offsetHeight/preview.clientHeight,visible:!el.classList.contains('hidden'),text:el.textContent,fontSize:parseFloat(getComputedStyle(el).fontSize)||22};
  }
  return {aspect:$('aspect').value,mirror:$('mirror').value==='yes',blurAmount:$('blurAmount').value,overlays:overlayData};
}
$('oneClick').addEventListener('click', async () => {
  if(!state.file) return toast('အရင် video upload လုပ်ပါ။',true);
  if(!state.apiKey) { $('apiPanel').classList.remove('hidden'); return toast('Gemini API key ထည့်ပြီး One-click ကို ထပ်နှိပ်ပါ။',true); }
  const b=$('oneClick'); b.disabled=true; b.textContent='① Transcribing…';
  try {
    showPage(2);
    const t=await api('/api/transcribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fileId:state.file.id})});
    const tj=await waitForJob(t.jobId,j=>{b.textContent='① Transcript '+(j.progress||0)+'%';$('transcribeStatus').textContent=j.message||'';});
    $('transcript').value=tj.transcript||''; saveProject();
    b.textContent='② Generating recap…';
    const rec=await api('/api/recap',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({transcript:$('transcript').value,language:$('language').value})});
    $('script').value=rec.script; saveProject();
    showPage(3); b.textContent='③ Generating AI voice…';
    const voice=await api('/api/voice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:rec.script})});
    state.voiceId=voice.voiceId; state.voiceUrl=voice.audioUrl; saveProject();
    $('voicePlayer').src=voice.audioUrl; $('voicePlayer').classList.remove('hidden'); $('voiceDownload').href=voice.audioUrl; $('voiceDownload').classList.remove('hidden');
    $('subtitleOverlay').textContent=rec.script.split(/[.!?\\n]/)[0].slice(0,110)||'မြန်မာ recap'; $('overlayText').value=$('subtitleOverlay').textContent;
    b.textContent='④ Exporting MP4…'; showPage(4);
    const exp=await api('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fileId:state.file.id,voiceId:state.voiceId,settings:buildExportSettings()})});
    $('exportProgressWrap').classList.remove('hidden');
    const ej=await waitForJob(exp.jobId,j=>{$('exportPct').textContent=(j.progress||0)+'%';$('exportProgress').style.width=(j.progress||0)+'%';$('exportLabel').textContent=j.message||'Rendering…';});
    $('exportResult').classList.remove('hidden');$('exportResult').innerHTML='<b>✓ Export complete</b><p>Your MP4 is ready.</p><a href="'+ej.downloadUrl+'" download>Download final MP4 ↗</a>';
    toast('One-click recap ပြီးပါပြီ။');
  } catch(e) { toast(e.message,true); }
  finally { b.disabled=false; b.textContent='⚡ One-click Recap'; }
});
$('toScript').addEventListener('click', () => showPage(2));
$('autoTranscribe').addEventListener('click', async () => {
  if (!state.file) return toast('အရင် video upload လုပ်ပါ။', true);
  if (!state.apiKey) $('apiPanel').classList.remove('hidden');
  const b=$('autoTranscribe'); b.disabled=true; b.textContent='Transcribing…'; $('transcribeStatus').textContent='Audio ကိုခွဲပြီး Gemini နဲ့ စစ်ဆေးနေပါတယ်။';
  try {
    const d=await api('/api/transcribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fileId:state.file.id})});
    const poll=async()=> {
      try {
        const job=await api('/api/jobs/'+d.jobId);
        $('transcribeStatus').textContent=(job.progress||0)+'% · '+(job.message||job.state);
        if(job.state==='done') { clearInterval(timer); $('transcript').value=job.transcript||''; saveProject(); b.disabled=false; b.textContent='♫ Auto Transcribe with Gemini'; $('transcribeStatus').textContent='Transcript ready · '+(job.chunkCount||0)+' audio chunks'; toast('Transcript ထုတ်ပြီးပါပြီ။'); }
        else if(job.state==='failed') { clearInterval(timer); b.disabled=false; b.textContent='♫ Retry Auto Transcribe'; $('transcribeStatus').textContent=job.message||'Transcription failed'; toast(job.message||'Transcription failed',true); }
      } catch(e) { clearInterval(timer); b.disabled=false; b.textContent='♫ Retry Auto Transcribe'; toast(e.message,true); }
    };
    var timer=setInterval(poll,2500); await poll();
  } catch(e) { b.disabled=false; b.textContent='♫ Retry Auto Transcribe'; $('transcribeStatus').textContent=e.message; toast(e.message,true); }
});
$('generateScript').addEventListener('click', async () => { const transcript=$('transcript').value.trim(); if(!state.apiKey) $('apiPanel').classList.remove('hidden'); if(!transcript) return toast('Transcript / story notes ထည့်ပါ။',true); const b=$('generateScript'); b.disabled=true; b.textContent='Generating…'; try { const d=await api('/api/recap',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({transcript,language:$('language').value})}); $('script').value=d.script; toast('Recap script ထုတ်ပြီးပါပြီ။'); } catch(e){toast(e.message,true);} finally {b.disabled=false;b.textContent='✦ Gemini နဲ့ Script ထုတ်မယ်';} });
$('toVoice').addEventListener('click', () => { if(!$('script').value.trim()) { toast('Script ကို ထည့်ပါ။',true); return; } $('subtitleOverlay').textContent=$('script').value.trim().split(/[.!?\n]/)[0].slice(0,110)||'မြန်မာ recap စာတန်း'; $('overlayText').value=$('subtitleOverlay').textContent; showPage(3); });
$('generateVoice').addEventListener('click', async () => { const text=$('script').value.trim(); if(!text) return toast('အရင် script ထည့်ပါ။',true); if(!state.apiKey) $('apiPanel').classList.remove('hidden'); const b=$('generateVoice'); b.disabled=true; b.textContent='Generating voice…'; try { const d=await api('/api/voice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})}); state.voiceUrl=d.audioUrl; state.voiceId=d.voiceId; saveProject(); $('voicePlayer').src=d.audioUrl; $('voicePlayer').classList.remove('hidden'); $('voiceDownload').href=d.audioUrl; $('voiceDownload').classList.remove('hidden'); toast('AI voice ထုတ်ပြီးပါပြီ။'); } catch(e){toast(e.message,true);} finally{b.disabled=false;b.textContent='♫ Generate Myanmar Voice';} });
$('aspect').addEventListener('change', e => { saveProject(); $('preview').classList.remove('aspect-916','aspect-169','aspect-11'); $('preview').classList.add(e.target.value==='9:16'?'aspect-916':e.target.value==='16:9'?'aspect-169':'aspect-11'); });
document.querySelectorAll('[data-tool]').forEach(b => b.addEventListener('click', () => { state.selectedTool=b.dataset.tool; document.querySelectorAll('[data-tool]').forEach(x=>x.classList.toggle('selected',x===b)); const map={subtitle:'subtitleOverlay',text:'textOverlay',logo:'logoOverlay',blur:'blurOverlay'}; const el=$(map[state.selectedTool]); el.classList.remove('hidden'); document.querySelectorAll('.overlay').forEach(x=>x.classList.toggle('selected',x===el)); if(state.selectedTool==='subtitle') $('overlayText').value=$('subtitleOverlay').textContent; if(state.selectedTool==='text') $('overlayText').value=$('textOverlay').textContent; if(state.selectedTool==='logo') $('overlayText').value=$('logoOverlay').textContent; }));
$('overlayText').addEventListener('input', e => { const map={subtitle:'subtitleOverlay',text:'textOverlay',logo:'logoOverlay'}; if(map[state.selectedTool]) $(map[state.selectedTool]).textContent=e.target.value; saveProject(); });
$('fontSize').addEventListener('input', e => { const el=$('subtitleOverlay'); el.style.fontSize=e.target.value+'px'; $('textOverlay').style.fontSize=e.target.value+'px'; saveProject(); });
$('blurAmount').addEventListener('change', e => $('blurOverlay').style.backdropFilter='blur('+Number(e.target.value)/2+'px)');
$('mirror').addEventListener('change', e => { $('previewVideo').style.transform=e.target.value==='yes'?'scaleX(-1)':'none'; saveProject(); });
$('toExport').addEventListener('click', () => { if(!state.file) return toast('Video upload လုပ်ပါ။',true); showPage(4); });
$('startExport').addEventListener('click', async () => {
  if(!state.file) return toast('Video upload လုပ်ပါ။',true);
  const b=$('startExport'); b.disabled=true; b.textContent='Export starting…'; $('exportProgressWrap').classList.remove('hidden'); $('exportResult').classList.add('hidden');
  try {
    const settings=buildExportSettings();
    const d=await api('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fileId:state.file.id,voiceId:state.voiceId,settings})});
    pollJob(d.jobId);
  } catch(e){toast(e.message,true);b.disabled=false;b.textContent='⚡ Export MP4';}
});
function pollJob(id) { clearInterval(state.jobPoll); const tick=async()=>{try{const d=await api('/api/jobs/'+id); $('exportPct').textContent=(d.progress||0)+'%'; $('exportProgress').style.width=(d.progress||0)+'%'; $('exportLabel').textContent=d.message||d.state; if(d.state==='done'){clearInterval(state.jobPoll);$('exportResult').classList.remove('hidden');$('exportResult').innerHTML='<b>✓ Export complete</b><p>Your MP4 is ready.</p><a href="'+d.downloadUrl+'" download>Download final MP4 ↗</a>'; $('startExport').disabled=false;$('startExport').textContent='Export again';toast('Export ပြီးပါပြီ။');} else if(d.state==='failed'){clearInterval(state.jobPoll);toast(d.message||'Export failed',true);$('startExport').disabled=false;$('startExport').textContent='⚡ Retry export';}}catch(e){clearInterval(state.jobPoll);toast(e.message,true);$('startExport').disabled=false;$('startExport').textContent='⚡ Retry export';}}; tick(); state.jobPoll=setInterval(tick,2500); }
let drag=null;
document.querySelectorAll('.overlay').forEach(el=>{
  el.addEventListener('pointerdown', e=>{ if(e.target.classList.contains('resize-handle')){drag={el,resize:true,x:e.clientX,y:e.clientY,w:el.offsetWidth,h:el.offsetHeight};} else {drag={el,resize:false,x:e.clientX,y:e.clientY,left:el.offsetLeft,top:el.offsetTop};} el.classList.add('selected'); el.setPointerCapture(e.pointerId); e.preventDefault(); });
  el.addEventListener('pointermove',e=>{if(!drag||drag.el!==el)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(drag.resize){el.style.width=Math.max(44,drag.w+dx)+'px';el.style.height=Math.max(25,drag.h+dy)+'px';}else{const p=$('preview');const left=Math.max(0,Math.min(p.clientWidth-el.offsetWidth,drag.left+dx));const top=Math.max(0,Math.min(p.clientHeight-el.offsetHeight,drag.top+dy));el.style.left=left+'px';el.style.top=top+'px';}});
  el.addEventListener('pointerup',()=>{drag=null;saveProject();});el.addEventListener('pointercancel',()=>{drag=null;saveProject();});
});
$('transcript').addEventListener('input', saveProject); $('script').addEventListener('input', saveProject); restoreProject();
api('/api/health').then(d=>{if(!d.ok)throw new Error('health');}).catch(()=>toast('Server connection မရပါ။ Deploy ပြီးမပြီး စစ်ပါ။',true));
