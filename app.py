import base64, io, json, re, shutil, subprocess, tempfile, wave
from pathlib import Path
import requests
import streamlit as st

st.set_page_config(page_title="Speed Recap", page_icon="🎬", layout="wide")
ROOT=Path("/var/data/speed-recap"); ROOT.mkdir(parents=True,exist_ok=True)
DEFAULTS={"video":"","original_srt":"","ai_srt":"","script":"","voice":"","final":"","groq":"","gemini":"","project":"speed_recap","segments":[],"duration":0.0,"voice_name":"Kore","speed":1.0,"edit":{}}
for k,v in DEFAULTS.items():
    if k not in st.session_state: st.session_state[k]=v
st.markdown("<style>.block-container{max-width:1250px;padding:.8rem .7rem 4rem}.stButton>button,.stDownloadButton>button{width:100%;min-height:2.5rem;border-radius:10px}[data-testid=stSidebar]{background:#101827}</style>",unsafe_allow_html=True)

def cmd(args,timeout=7200):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or "Command failed")[-5000:])
    return p.stdout.strip()
def tsec(x):
    ms=max(0,int(round(float(x)*1000))); h,r=divmod(ms,3600000); m,r=divmod(r,60000); s,ms=divmod(r,1000); return f"{h:02}:{m:02}:{s:02},{ms:03}"
def parse_time(v):
    h,m,z=v.replace('.',',').split(':'); q,ms=(z.split(',')+["0"])[:2]; return int(h)*3600+int(m)*60+int(q)+int(ms.ljust(3,'0')[:3])/1000
def parse_srt(s):
    out=[]
    for b in re.split(r'\n\s*\n',s.replace('\r','').strip()):
        lines=[x.strip() for x in b.split('\n') if x.strip()]; tl=next((x for x in lines if '-->' in x),None)
        if not tl: continue
        a,z=[x.strip() for x in tl.split('-->',1)]; text=' '.join(x for x in lines[lines.index(tl)+1:] if not x.isdigit())
        if text: out.append([parse_time(a),parse_time(z),text])
    return out
def make_srt(segs): return '\n\n'.join(f"{i}\n{tsec(a)} --> {tsec(b)}\n{text}" for i,(a,b,text) in enumerate(segs,1))+'\n'
def gemini(key,prompt,model="gemini-2.5-flash"):
    r=requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",params={'key':key},json={'contents':[{'parts':[{'text':prompt}]}]},timeout=240)
    if not r.ok: raise RuntimeError(f"Gemini {r.status_code}: {r.text[:1500]}")
    try:return ''.join(p.get('text','') for p in r.json()['candidates'][0]['content']['parts'])
    except Exception: raise RuntimeError('Gemini က စာသားမပြန်ပါ။ API key/model/quota စစ်ပါ။')
def download_url(url,out):
    if not shutil.which('yt-dlp'): raise RuntimeError('yt-dlp မတွေ့ပါ။ Render dependency ကို update လုပ်ပါ။')
    cmd(['yt-dlp','--no-playlist','-f','bv*+ba/b','--merge-output-format','mp4','-o',str(out),url],3600)
def transcribe(video,key,work):
    audio=work/'audio.mp3'; cmd(['ffmpeg','-y','-i',str(video),'-vn','-ac','1','-ar','16000','-b:a','48k',str(audio)],3600)
    with open(audio,'rb') as f: r=requests.post('https://api.groq.com/openai/v1/audio/transcriptions',headers={'Authorization':'Bearer '+key},data={'model':'whisper-large-v3-turbo','response_format':'verbose_json','temperature':'0'},files={'file':(audio.name,f,'audio/mpeg')},timeout=1800)
    if not r.ok: raise RuntimeError(f"Groq {r.status_code}: {r.text[:1500]}")
    d=r.json(); seg=[]
    for x in d.get('segments',[]):
        text=str(x.get('text','')).strip(); a=float(x.get('start',0)); b=float(x.get('end',a))
        if text: seg.append([a,b,text])
    if not seg: raise RuntimeError('အသံမှ စာသားမထုတ်နိုင်ပါ။')
    return seg,float(d.get('duration',0) or 0)
def translate_segments(key,segs):
    payload=json.dumps([{'i':i,'start':a,'end':b,'text':t} for i,(a,b,t) in enumerate(segs,1)],ensure_ascii=False)
    prompt="Translate every movie transcript item into natural spoken Myanmar Burmese for a movie recap narrator. Keep EXACTLY the same i/start/end values. Do not omit, merge, reorder, or add items. Return ONLY valid JSON array with keys i,start,end,text. Keep each Burmese text concise enough to fit its original time. No markdown.\n"+payload
    raw=re.sub(r'^```(?:json)?\s*|\s*```$','',gemini(key,prompt).strip()); data=json.loads(raw); by={int(x['i']):x for x in data}; return [[a,b,str(by.get(i,{}).get('text',t)).strip()] for i,(a,b,t) in enumerate(segs,1)]
def tts_one(key,text,voice,out,speed):
    prompt=f"Speak only this Myanmar Burmese narration naturally and clearly. Do not add words. Target speed {speed:.2f}x:\n{text}"
    p={'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'responseModalities':['AUDIO'],'speechConfig':{'voiceConfig':{'prebuiltVoiceConfig':{'voiceName':voice}}}}}
    r=requests.post('https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent',params={'key':key},json=p,timeout=300)
    if not r.ok: raise RuntimeError(f"Gemini TTS {r.status_code}: {r.text[:1500]}")
    d=r.json(); part=next((p for p in d['candidates'][0]['content']['parts'] if 'inlineData' in p),None)
    if not part: raise RuntimeError('TTS audio data မရပါ။')
    raw=base64.b64decode(part['inlineData']['data']); mime=part['inlineData'].get('mimeType','audio/L16;rate=24000'); m=re.search(r'rate=(\d+)',mime); rate=int(m.group(1)) if m else 24000
    if 'wav' in mime.lower():
        with wave.open(io.BytesIO(raw),'rb') as w: pcm=w.readframes(w.getnframes()); rate=w.getframerate()
    else: pcm=raw
    with wave.open(str(out),'wb') as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
def build_timed_voice(key,segs,voice,speed,work):
    pieces=[]
    for i,(a,b,text) in enumerate(segs):
        p=work/f'v_{i:04}.wav'; tts_one(key,text,voice,p,speed); pieces.append((a,p))
    inputs=[]; filters=[]; labels=[]
    for i,(a,p) in enumerate(pieces):
        inputs+=['-i',str(p)]; d=max(0,int(a*1000)); filters.append(f'[{i}:a]adelay={d}|{d}[v{i}]'); labels.append(f'[v{i}]')
    filters.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=longest:dropout_transition=0[aout]')
    final=work/'ai_voice_timed.wav'; cmd(['ffmpeg','-y']+inputs+['-filter_complex',';'.join(filters),'-map','[aout]','-c:a','pcm_s16le',str(final)],3600); return final
def esc(p): return str(p).replace('\\','/').replace(':','\\:').replace("'","\\'").replace('[','\\[').replace(']','\\]')
def render(video,out,segs,voice=None,edit=None,music=None):
    edit=edit or {}; work=out.parent; sp=work/'ai_voice.srt'; sp.write_text(make_srt(segs),encoding='utf-8')
    x=edit.get('bx',5)/100; y=edit.get('by',5)/100; w=edit.get('bw',90)/100; h=edit.get('bh',90)/100
    base='[0:v]'
    if edit.get('mirror'): base+='hflip,'
    if edit.get('autocolor'): base+='eq=contrast=1.04:brightness=0.02:saturation=1.10,'
    if edit.get('blur_on'): graph=base+f"split=2[base][bl];[bl]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},boxblur=12:2[bb];[base][bb]overlay=iw*{x}:ih*{y}[vb];[vb]subtitles='{esc(sp)}':force_style='FontName=AKA03,FontSize={int(edit.get('subsize',26))},Outline=2,Shadow=1,Alignment=2,MarginV={int(edit.get('submargin',35))}'[vout]"
    else: graph=base+f"subtitles='{esc(sp)}':force_style='FontName=AKA03,FontSize={int(edit.get('subsize',26))},Outline=2,Shadow=1,Alignment=2,MarginV={int(edit.get('submargin',35))}'[vout]"
    logo=edit.get('logo'); has_logo=bool(logo and Path(logo).exists()); has_voice=bool(voice and Path(voice).exists()); has_music=bool(music and Path(music).exists())
    args=['ffmpeg','-y','-i',str(video)]; idx=1
    if has_logo: args+=['-i',str(logo)]; li=idx; idx+=1
    if has_voice: args+=['-i',str(voice)]; vi=idx; idx+=1
    if has_music: args+=['-i',str(music)]; mi=idx; idx+=1
    vmap='[vout]'
    if has_logo: graph+=f";[{li}:v]scale=iw*0.18:-1[lg];[vout][lg]overlay=W-w-24:24[vfinal]"; vmap='[vfinal]'
    aud=[]; amap='0:a?'
    if has_voice:
        aud=[f'[0:a]volume={max(0,min(100,int(edit.get("origvol",20))))/100}[oa]',f'[{vi}:a]volume=1[va]']; labels=['[oa]','[va]']
        if has_music: aud.append(f'[{mi}:a]volume={max(0,min(100,int(edit.get("musicvol",12))))/100}[ma]'); labels.append('[ma]')
        aud.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=longest:dropout_transition=0[aout]'); amap='[aout]'
    elif has_music:
        aud=[f'[0:a]volume=0.35[oa]',f'[{mi}:a]volume={max(0,min(100,int(edit.get("musicvol",12))))/100}[ma]','[oa][ma]amix=inputs=2:duration=first[aout]']; amap='[aout]'
    args+=['-filter_complex',';'.join([graph]+aud),'-map',vmap,'-map',amap,'-c:v','libx264','-preset','veryfast','-crf','23','-c:a','aac','-b:a','160k','-shortest','-movflags','+faststart',str(out)]; cmd(args,7200); return out

with st.sidebar:
    st.header('⚙️ Settings'); st.session_state.groq=st.text_input('Groq API Key',value=st.session_state.groq,type='password'); st.session_state.gemini=st.text_input('Gemini API Key',value=st.session_state.gemini,type='password')
    st.markdown('[Groq Key](https://console.groq.com/keys) · [Gemini Key](https://aistudio.google.com/apikey)'); st.session_state.project=st.text_input('Project name',value=st.session_state.project)
    if st.button('🧹 New Project'):
        for k,v in DEFAULTS.items(): st.session_state[k]=v
        st.rerun()
st.title('🎬 Speed Recap'); st.caption('Video/URL → Groq Transcript → Gemini Burmese Script → AI Voice SRT → Live Edit → Final MP4')
t1,t2,t3,t4=st.tabs(['1️⃣ Source & SRT','2️⃣ AI Voice','3️⃣ Live Edit','4️⃣ Final Video'])
with t1:
    st.subheader('Step 1 — Video / URL'); mode=st.radio('Source',['Upload Video','Video URL'],horizontal=True); video_file=None; url=''
    if mode=='Upload Video': video_file=st.file_uploader('MP4 / MOV / MKV / WEBM',type=['mp4','mov','mkv','webm','avi'])
    else: url=st.text_input('Video URL')
    if st.button('🚀 Step 1 စတင်မယ်',type='primary'):
        if not st.session_state.groq: st.error('Groq API Key ထည့်ပါ။')
        elif not video_file and not url.strip(): st.error('Video upload သို့မဟုတ် URL ထည့်ပါ။')
        else:
            try:
                work=Path(tempfile.mkdtemp(prefix='speed_',dir=ROOT)); video=work/'source.mp4'
                if video_file:
                    with open(video,'wb') as f:
                        while True:
                            b=video_file.read(1024*1024)
                            if not b: break
                            f.write(b)
                else: download_url(url.strip(),video)
                seg,dur=transcribe(video,st.session_state.groq,work); st.session_state.video=str(video); st.session_state.segments=seg; st.session_state.duration=dur; st.session_state.original_srt=make_srt(seg); st.success(f'Groq transcript ပြီးပါပြီ — {len(seg)} segments')
            except Exception as e: st.error(str(e))
    if st.session_state.original_srt:
        st.download_button('⬇️ Original SRT',st.session_state.original_srt,f'{st.session_state.project}_original.srt','text/plain')
        if st.session_state.gemini and st.button('🇲🇲 Burmese Script + AI Voice SRT ထုတ်မယ်'):
            try:
                b=translate_segments(st.session_state.gemini,st.session_state.segments); st.session_state.segments=b; st.session_state.ai_srt=make_srt(b); st.session_state.script='\n'.join(x[2] for x in b); st.success('Burmese timeline SRT ပြီးပါပြီ။')
            except Exception as e: st.error(str(e))
        if st.session_state.ai_srt: st.download_button('⬇️ AI Voice SRT',st.session_state.ai_srt,f'{st.session_state.project}_ai_voice.srt','text/plain')
with t2:
    st.subheader('Step 2 — AI Voice')
    if not st.session_state.ai_srt: st.info('Step 1 မှာ Burmese Script ကို အရင်ထုတ်ပါ။')
    else:
        voices=['Kore','Puck','Charon','Fenrir']; voice=st.selectbox('Burmese Voice',voices,index=voices.index(st.session_state.voice_name) if st.session_state.voice_name in voices else 0); speed=st.select_slider('Speed',options=[0.75,0.85,0.95,1.0,1.10,1.20,1.30],value=st.session_state.speed)
        st.caption('15–24 = slow · 25–30 = normal · 30–35 = long. AI voice ကို original video timeline အတိုင်း စီထားပါတယ်။')
        if st.button('🎙️ AI Voice Generate',type='primary'):
            if not st.session_state.gemini: st.error('Gemini API Key ထည့်ပါ။')
            else:
                try:
                    work=Path(st.session_state.video).parent; st.session_state.voice=str(build_timed_voice(st.session_state.gemini,st.session_state.segments,voice,float(speed),work)); st.session_state.voice_name=voice; st.session_state.speed=float(speed); st.success('AI Voice timeline sync ပြီးပါပြီ။')
                except Exception as e: st.error(str(e))
        if st.session_state.voice and Path(st.session_state.voice).exists(): st.audio(st.session_state.voice); st.download_button('⬇️ AI Voice',open(st.session_state.voice,'rb').read(),f'{st.session_state.project}_voice.wav','audio/wav')
with t3:
    st.subheader('Step 3 — Live Edit')
    if not st.session_state.video: st.info('Step 1 ကို အရင်လုပ်ပါ။')
    else:
        st.video(st.session_state.video); e=st.session_state.edit
        e['blur_on']=st.toggle('Blur ON/OFF',value=e.get('blur_on',True)); e['mirror']=st.toggle('Mirror ON/OFF',value=e.get('mirror',False)); e['autocolor']=st.toggle('Auto Colour ON/OFF',value=e.get('autocolor',True))
        c1,c2=st.columns(2)
        with c1: e['bx']=st.slider('Blur X %',0,90,int(e.get('bx',5))); e['by']=st.slider('Blur Y %',0,90,int(e.get('by',5))); e['bw']=st.slider('Blur Width %',5,100,int(e.get('bw',90))); e['bh']=st.slider('Blur Height %',5,100,int(e.get('bh',90)))
        with c2: e['subsize']=st.slider('Subtitle size',12,60,int(e.get('subsize',26))); e['submargin']=st.slider('Subtitle position',0,180,int(e.get('submargin',35))); e['origvol']=st.slider('Original audio %',0,100,int(e.get('origvol',20)))
        logo=st.file_uploader('Logo (optional)',type=['png','jpg','jpeg'],key='logo_upload'); music=st.file_uploader('Background Music (optional)',type=['mp3','wav','m4a'],key='music_upload')
        if logo: lp=Path(st.session_state.video).parent/'logo.png'; lp.write_bytes(logo.getbuffer()); e['logo']=str(lp)
        if music: mp=Path(st.session_state.video).parent/'music.mp3'; mp.write_bytes(music.getbuffer()); e['music']=str(mp)
        e['musicvol']=st.slider('Music volume %',0,50,int(e.get('musicvol',12))); st.session_state.edit=e
        if st.button('👀 Preview Edit'):
            try:
                p=Path(st.session_state.video).parent/'preview.mp4'; render(st.session_state.video,p,st.session_state.segments,st.session_state.voice,e,e.get('music')); st.video(str(p))
            except Exception as ex: st.error(str(ex))
with t4:
    st.subheader('Step 4 — Final Video')
    if not st.session_state.video or not st.session_state.ai_srt: st.info('Step 1 + Step 2 ကို အရင်ပြီးအောင်လုပ်ပါ။')
    else:
        st.write('AI Voice + Video + AI Voice SRT + Live Edit ကို timeline တစ်ခုထဲမှာပေါင်းမယ်။')
        if st.button('🎬 FINAL MP4 ထုတ်မယ်',type='primary'):
            try:
                p=Path(st.session_state.video).parent/f'{st.session_state.project}_final.mp4'; render(st.session_state.video,p,st.session_state.segments,st.session_state.voice,st.session_state.edit,st.session_state.edit.get('music')); st.session_state.final=str(p); st.success('Final Video အောင်မြင်ပါပြီ။')
            except Exception as e: st.error(str(e))
        if st.session_state.final and Path(st.session_state.final).exists(): st.video(st.session_state.final); st.download_button('⬇️ Download Final MP4',open(st.session_state.final,'rb').read(),f'{st.session_state.project}_final.mp4','video/mp4')
st.divider(); st.caption('API keys ကို source code ထဲမသိမ်းပါ။ ကိုယ်ပိုင်/ခွင့်ပြုချက်ရှိသော video များကိုသာ အသုံးပြုပါ။')
