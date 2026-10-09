import base64
import re
import subprocess
import tempfile
from pathlib import Path
import requests
import streamlit as st

st.set_page_config(page_title="Lynn Recap Studio", page_icon="🎬", layout="wide")
st.markdown("""<style>.block-container{max-width:1200px;padding-top:1rem}div[data-testid="stButton"] button,div[data-testid="stDownloadButton"] button{width:100%;min-height:2.7rem;border-radius:10px}</style>""", unsafe_allow_html=True)
st.title("🎬 Lynn Recap Studio")
st.caption("Video Upload → Fast SRT → မြန်မာဘာသာပြန် → Gemini AI Voice → Live Edit → Final MP4 + Thumbnail")

DEFAULTS={"original_srt":"","burmese_srt":"","voice_bytes":None,"final_bytes":None,"thumbnail_bytes":None,"video_bytes":None,"video_name":"","project_name":"lynn_recap"}
for k,v in DEFAULTS.items():
    if k not in st.session_state: st.session_state[k]=v

with st.sidebar:
    st.header("🔑 API Keys")
    # Keys are kept in Streamlit session state for the current active session.
    if "saved_groq_key" not in st.session_state: st.session_state.saved_groq_key = ""
    if "saved_gemini_key" not in st.session_state: st.session_state.saved_gemini_key = ""
    if "groq_key_input" not in st.session_state: st.session_state.groq_key_input = st.session_state.saved_groq_key
    if "gemini_key_input" not in st.session_state: st.session_state.gemini_key_input = st.session_state.saved_gemini_key
    st.text_input("Groq API Key (SRT အမြန်ထုတ်ရန်)",type="password",key="groq_key_input")
    st.markdown("[Groq Key ရယူရန်](https://console.groq.com/keys)")
    st.text_input("Gemini API Key",type="password",key="gemini_key_input")
    st.markdown("[Gemini Key ရယူရန်](https://aistudio.google.com/apikey)")
    if st.button("💾 API Keys သိမ်းမယ်",use_container_width=True):
        st.session_state.saved_groq_key = st.session_state.groq_key_input.strip()
        st.session_state.saved_gemini_key = st.session_state.gemini_key_input.strip()
        st.success("ဒီ session အတွက် API Keys သိမ်းပြီးပါပြီ။")
    groq_key = st.session_state.saved_groq_key
    gemini_key = st.session_state.saved_gemini_key
    st.divider()
    st.session_state.project_name=st.text_input("Output ဖိုင်နာမည်",st.session_state.project_name).strip() or "lynn_recap"
    if st.button("🧹 New Project / အစမှပြန်စမယ်"):
        for k,v in DEFAULTS.items(): st.session_state[k]=v
        st.rerun()
    st.caption("Session ပြတ်လျှင် မသိမ်းရသေးသောအလုပ် ပျောက်နိုင်သည်။")

def cmd(args):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or "FFmpeg error")[-2500:])
    return p.stdout.strip()

def stamp(sec):
    ms=max(0,int(round(float(sec)*1000))); h,rem=divmod(ms,3600000); m,rem=divmod(rem,60000); s,ms=divmod(rem,1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def transcribe(video,key,folder):
    cmd(["ffmpeg","-y","-i",video,"-vn","-ac","1","-ar","16000","-b:a","48k","-f","segment","-segment_time","600","-reset_timestamps","1",str(Path(folder)/"part_%03d.mp3")])
    parts=sorted(Path(folder).glob("part_*.mp3"))
    if not parts: raise RuntimeError("အသံမတွေ့ပါ။ Audio ပါသော video ကိုရွေးပါ။")
    segments=[]; offset=0.0
    for part in parts:
        with open(part,"rb") as f:
            r=requests.post("https://api.groq.com/openai/v1/audio/transcriptions",headers={"Authorization":"Bearer "+key},data={"model":"whisper-large-v3-turbo","response_format":"verbose_json","temperature":"0"},files={"file":(part.name,f,"audio/mpeg")},timeout=300)
        if not r.ok: raise RuntimeError(f"Groq API {r.status_code}: {r.text[:700]}")
        data=r.json()
        for x in data.get("segments",[]): segments.append((float(x.get("start",0))+offset,float(x.get("end",0))+offset,str(x.get("text","")).strip()))
        offset+=float(cmd(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(part)]))
    return "\n\n".join(f"{i}\n{stamp(a)} --> {stamp(b)}\n{t}" for i,(a,b,t) in enumerate((x for x in segments if x[2]),1))+"\n"

def translate(srt,key):
    prompt="Translate subtitle dialogue into natural spoken Burmese. Keep every number and timestamp unchanged. Return only SRT.\n\n"+srt[:180000]
    error="Gemini ဘာသာပြန်မအောင်မြင်ပါ။"
    for model in ["gemini-2.5-flash","gemini-2.0-flash"]:
        try:
            r=requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",params={"key":key},json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.2}},timeout=180)
            if not r.ok: error=f"{r.status_code}: {r.text[:500]}"; continue
            answer="".join(p.get("text","") for p in r.json()["candidates"][0]["content"]["parts"]).strip()
            answer=answer.strip(chr(96)).strip()
            if re.search(r"\d+\s*\n\d{2}:\d{2}:\d{2},\d{3}\s*-->",answer): return answer+"\n"
            error="Gemini က SRT ပုံစံမှန်မပြန်ပါ။"
        except Exception as e: error=str(e)
    raise RuntimeError(error)

def voice_bytes(text,key,voice,speed):
    if not key: raise RuntimeError("Gemini API Key ထည့်ပါ။")
    if not text.strip(): raise RuntimeError("အသံထုတ်မည့်စာသား မရှိပါ။")
    prompt = "Read the following text naturally in clear spoken Myanmar Burmese. Do not translate, summarize, or add words. Speaking speed preference: " + ("slightly slower" if speed < 0.9 else "slightly faster" if speed > 1.1 else "natural pace") + ".\n\n" + text[:12000]
    error = "Gemini TTS မအောင်မြင်ပါ။"
    for model in ["gemini-2.5-flash-preview-tts","gemini-2.5-pro-preview-tts"]:
        try:
            r=requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",params={"key":key},json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseModalities":["AUDIO"],"speechConfig":{"voiceConfig":{"prebuiltVoiceConfig":{"voiceName":voice}}}}},timeout=300)
            if not r.ok:
                error=f"Gemini TTS {r.status_code}: {r.text[:700]}"
                continue
            data=r.json()
            part=next((p for p in data.get("candidates",[{}])[0].get("content",{}).get("parts",[]) if p.get("inlineData",{}).get("data")),None)
            if not part: error="Gemini TTS က audio မပြန်ပေးပါ။"; continue
            raw=base64.b64decode(part["inlineData"]["data"])
            mime=part["inlineData"].get("mimeType","audio/pcm;rate=24000")
            rate_match=re.search(r"rate=(\d+)",mime)
            sample_rate=int(rate_match.group(1)) if rate_match else 24000
            channels=1; bits=16
            header=b"RIFF"+(36+len(raw)).to_bytes(4,"little")+b"WAVEfmt "+(16).to_bytes(4,"little")+(1).to_bytes(2,"little")+channels.to_bytes(2,"little")+sample_rate.to_bytes(4,"little")+(sample_rate*channels*bits//8).to_bytes(4,"little")+(channels*bits//8).to_bytes(2,"little")+bits.to_bytes(2,"little")+b"data"+len(raw).to_bytes(4,"little")
            return header+raw
        except Exception as e: error=str(e)
    raise RuntimeError(error)

def dialogue(srt):
    lines=[]
    for line in srt.splitlines():
        x=line.strip()
        if x and not x.isdigit() and "-->" not in x: lines.append(x)
    return " ".join(lines)

def esc(path): return str(path).replace("\\","/").replace(":","\\:").replace("'","\\'")

@st.cache_data(show_spinner=False)
def font():
    # Find a Myanmar-capable font once; avoid a full filesystem scan on every Streamlit rerun.
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansMyanmar-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansMyanmar-VF.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansMyanmar-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSerifMyanmar-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ]
    for p in candidates:
        if Path(p).is_file():
            return p
    try:
        found = list(Path("/usr/share/fonts").rglob("*Myanmar*"))
        for p in found:
            if p.is_file() and p.suffix.lower() in (".ttf", ".otf"):
                return str(p)
    except Exception:
        pass
    return ""

@st.cache_data(show_spinner=False, max_entries=4)
def extract_preview_frame(video_bytes, video_name, second):
    with tempfile.TemporaryDirectory() as td:
        ext=Path(video_name or "video.mp4").suffix or ".mp4"
        vp=Path(td)/("preview"+ext); fp=Path(td)/"frame.jpg"
        vp.write_bytes(video_bytes)
        cmd(["ffmpeg","-hide_banner","-loglevel","error","-y","-ss",str(second),"-i",str(vp),"-frames:v","1","-vf","scale=720:-2","-q:v","4",str(fp)])
        if not fp.exists(): raise RuntimeError("Preview frame မထုတ်နိုင်ပါ။ Preview အချိန်ကို ပြောင်းကြည့်ပါ။")
        return fp.read_bytes()

tabs=st.tabs(["① Video Upload","② Transcript + Translate","③ AI Voice","④ Final Video + Thumbnail"])
with tabs[0]:
    st.subheader("Video Upload")
    up=st.file_uploader("Video ရွေးပါ (MP4/MOV/MKV/WEBM/AVI)",type=["mp4","mov","mkv","webm","avi"],key="video_up")
    if up:
        st.session_state.video_bytes=up.getvalue(); st.session_state.video_name=up.name
    if st.session_state.video_bytes:
        st.video(st.session_state.video_bytes)
        st.caption(f"{st.session_state.video_name} · {len(st.session_state.video_bytes)/1048576:.1f} MB")
    st.info("Streamlit Cloud မှာ ဖိုင်ကြီးတွေက memory/resource ကန့်သတ်ချက်ကြောင့် မအောင်မြင်နိုင်ပါ။ 900MB ဖိုင်များအတွက် ပိုအားကောင်းသော server လိုနိုင်သည်။")

with tabs[1]:
    st.subheader("Original Transcript / SRT")
    if st.button("🎧 Original SRT ထုတ်မယ်",disabled=not st.session_state.video_bytes or not groq_key):
        with st.spinner("Whisper transcription လုပ်နေပါတယ်..."):
            try:
                with tempfile.TemporaryDirectory() as td:
                    ext=Path(st.session_state.video_name or "video.mp4").suffix or ".mp4"
                    vp=Path(td)/("input"+ext); vp.write_bytes(st.session_state.video_bytes)
                    st.session_state.original_srt=transcribe(str(vp),groq_key,td)
                st.session_state.burmese_srt=""; st.session_state.voice_bytes=None
                st.success("Original SRT ပြီးပါပြီ။")
            except Exception as e: st.error(str(e))
    st.text_area("Original SRT (ပြင်နိုင်သည်)",key="original_srt",height=240)
    if st.session_state.original_srt: st.download_button("⬇️ Original SRT Download",st.session_state.original_srt,"original.srt","application/x-subrip")
    st.divider(); st.subheader("မြန်မာဘာသာပြန်")
    if st.button("🇲🇲 Gemini နဲ့ ဘာသာပြန်မယ်",disabled=not st.session_state.original_srt.strip() or not gemini_key):
        with st.spinner("ဘာသာပြန်နေပါတယ်..."):
            try: st.session_state.burmese_srt=translate(st.session_state.original_srt,gemini_key); st.success("ဘာသာပြန်ပြီးပါပြီ။")
            except Exception as e: st.error(str(e))
    st.text_area("Burmese SRT (ပြင်ဆင်နိုင်သည်)",key="burmese_srt",height=260)
    if st.session_state.burmese_srt: st.download_button("⬇️ Burmese SRT Download",st.session_state.burmese_srt,"burmese.srt","application/x-subrip")

with tabs[2]:
    st.subheader("AI Voice")
    vc=st.selectbox("Gemini အသံရွေးပါ",["မြန်မာ အမျိုးသမီး — Kore","မြန်မာ အမျိုးသား — Puck"])
    speed=st.slider("Voice Speed",0.7,1.3,1.0,0.1)
    if st.button("🎙️ Gemini AI Voice ထုတ်မယ်",disabled=not st.session_state.burmese_srt.strip() or not gemini_key):
        name="Kore" if vc.startswith("မြန်မာ အမျိုးသမီး") else "Puck"
        with st.spinner("Gemini AI Voice ဖန်တီးနေပါတယ်..."):
            try: st.session_state.voice_bytes=voice_bytes(dialogue(st.session_state.burmese_srt),gemini_key,name,speed); st.success("Gemini Voice ပြီးပါပြီ။")
            except Exception as e: st.error(str(e))
    if st.session_state.voice_bytes:
        st.audio(st.session_state.voice_bytes)
        st.download_button("⬇️ AI Voice Download",st.session_state.voice_bytes,f"{st.session_state.project_name}_voice.wav","audio/wav")

with tabs[3]:
    st.subheader("🎬 Live Edit Preview")
    st.caption("Blur၊ Subtitle၊ Logo နဲ့ စာသားကို Live Edit ပေါ်မှာ တိုက်ရိုက်ဖိဆွဲပြီး နေရာ/အရွယ်အစား ပြင်ပါ။ သီးခြား X/Y Adjust slider တွေ မလိုပါ။")

    for key, default in (("blur_enabled", False), ("burn_subtitles", True), ("mirror_enabled", False),
                         ("mix_original_audio", False), ("original_audio_volume", 15),
                         ("blur_amount", 5), ("overlay_text", ""), ("subtitle_size", 24)):
        if key not in st.session_state:
            st.session_state[key] = default
    if "live_positions" not in st.session_state:
        st.session_state.live_positions = {
            "blur": {"x": 25, "y": 25, "w": 25, "h": 25},
            "subtitle": {"x": 20, "y": 82, "scale": 1.0},
            "text": {"x": 5, "y": 8, "scale": 1.0},
            "logo": {"x": 4, "y": 5, "scale": 1.0},
        }

    c1,c2=st.columns(2)
    with c1:
        st.checkbox("🌫️ Blur ထည့်မယ်",key="blur_enabled")
        burn=st.checkbox("မြန်မာစာတန်းထိုး",True,key="burn_subtitles")
        fontsize=st.slider("စာတန်းအရွယ်အစား",14,42,24,key="subtitle_size")
        mirror=st.checkbox("Mirror / ဘယ်ညာပြောင်း",False,key="mirror_enabled")
        blur_amount=st.slider("Blur အား",1,20,5,key="blur_amount",disabled=not st.session_state.blur_enabled)
        blur=blur_amount if st.session_state.blur_enabled else 0
    with c2:
        mix=st.checkbox("မူရင်းအသံကို နောက်ခံအဖြစ်ထားမယ်",False,key="mix_original_audio")
        vol=st.slider("မူရင်းအသံ Volume (%)",0,100,15,key="original_audio_volume")
        text_value=st.text_input("Live Edit ပေါ်တင်မည့်စာသား",placeholder="Lynn Recap",key="overlay_text")
        logo_upload=st.file_uploader("Logo ပုံ (PNG/JPG)",type=["png","jpg","jpeg"],key="logo_upload")
    text=text_value.strip()
    logo=logo_upload

    # Positions and scale are driven by direct manipulation on the Live Edit canvas.
    lp=st.session_state.live_positions
    blur_x,blur_y,blur_w,blur_h=lp["blur"]["x"],lp["blur"]["y"],lp["blur"]["w"],lp["blur"]["h"]
    tx,ty=lp["text"]["x"],lp["text"]["y"]
    logo_x,logo_y=lp["logo"]["x"],lp["logo"]["y"]
    subtitle_y=lp["subtitle"]["y"]
    pos="အပေါ်" if subtitle_y < 50 else "အောက်"

    preview_second=st.number_input("Preview အချိန် (စက္ကန့်)",min_value=0.0,max_value=36000.0,value=1.0,step=1.0,key="preview_second")
    if st.session_state.video_bytes:
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageFilter
            from streamlit_drawable_canvas import st_canvas
            import io, base64
            frame=Image.open(io.BytesIO(extract_preview_frame(st.session_state.video_bytes,st.session_state.video_name,preview_second))).convert("RGB")
            if mirror: frame=frame.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if blur:
                bx=min(frame.width-1,int(frame.width*blur_x/100)); by=min(frame.height-1,int(frame.height*blur_y/100))
                bw=max(1,min(frame.width-bx,int(frame.width*blur_w/100))); bh=max(1,min(frame.height-by,int(frame.height*blur_h/100)))
                roi=frame.crop((bx,by,bx+bw,by+bh)).filter(ImageFilter.GaussianBlur(radius=max(1,blur/2)))
                frame.paste(roi,(bx,by))
            # Blur the selected region in the preview; its rectangle is draggable/resizable on canvas.
            if blur:
                bx=min(frame.width-1,int(frame.width*blur_x/100)); by=min(frame.height-1,int(frame.height*blur_y/100))
                bw=max(1,min(frame.width-bx,int(frame.width*blur_w/100))); bh=max(1,min(frame.height-by,int(frame.height*blur_h/100)))
                roi=frame.crop((bx,by,bx+bw,by+bh)).filter(ImageFilter.GaussianBlur(radius=max(1,blur/2)))
                frame.paste(roi,(bx,by))

            def parse_sec(ts):
                hh,mm,rest=ts.split(":"); ss,ms=rest.split(",")
                return int(hh)*3600+int(mm)*60+int(ss)+int(ms)/1000
            active=""
            if burn and st.session_state.burmese_srt.strip():
                for subtitle_block in re.split(r"\n\s*\n",st.session_state.burmese_srt.strip()):
                    lines=subtitle_block.splitlines()
                    if len(lines)>=3 and "-->" in lines[1]:
                        try:
                            aa,bb=[parse_sec(z.strip()) for z in lines[1].split("-->")]
                            if aa<=preview_second<=bb: active=" ".join(lines[2:]); break
                        except Exception: pass

            # All adjustable items are objects on this canvas, including subtitle and blur region.
            drawing={"version":"4.4.0","objects":[]}
            if st.session_state.blur_enabled:
                bp=lp["blur"]
                drawing["objects"].append({"type":"rect","version":"4.4.0","name":"overlay_blur",
                    "left":int(frame.width*bp["x"]/100),"top":int(frame.height*bp["y"]/100),
                    "width":max(20,int(frame.width*bp["w"]/100)),"height":max(20,int(frame.height*bp["h"]/100)),
                    "fill":"rgba(80,160,255,0.18)","stroke":"#55aaff","strokeWidth":2,"scaleX":1,"scaleY":1})
            if active:
                fp=font()
                try: sf=ImageFont.truetype(fp,fontsize) if fp else ImageFont.load_default()
                except Exception: sf=ImageFont.load_default()
                si=Image.new("RGBA",(frame.width,100),(0,0,0,0)); sd=ImageDraw.Draw(si)
                words=list(active); wrapped=[]; line=""
                for ch in words:
                    if sd.textbbox((0,0),line+ch,font=sf,stroke_width=2)[2] > frame.width-32 and line:
                        wrapped.append(line); line=ch
                    else: line+=ch
                if line: wrapped.append(line)
                sy=4
                for ln in wrapped[:3]:
                    sd.text((4,sy),ln,font=sf,fill="white",stroke_width=2,stroke_fill="black"); sy+=max(24,sd.textbbox((0,0),ln,font=sf,stroke_width=2)[3]+4)
                sb=si.getbbox(); si=si.crop(sb) if sb else si
                buf=io.BytesIO(); si.save(buf,format="PNG"); ssrc="data:image/png;base64,"+base64.b64encode(buf.getvalue()).decode("ascii")
                sp=lp["subtitle"]
                drawing["objects"].append({"type":"image","version":"4.4.0","name":"overlay_subtitle",
                    "left":int(frame.width*sp["x"]/100),"top":int(frame.height*sp["y"]/100),
                    "width":si.width,"height":si.height,"scaleX":sp["scale"],"scaleY":sp["scale"],
                    "src":ssrc,"crossOrigin":"anonymous"})
            if text.strip():
                # Render overlay text with the Myanmar font into a transparent PNG for reliable preview.
                fp=font(); tf=ImageFont.truetype(fp,28) if fp else ImageFont.load_default()
                tw=max(220,frame.width//2); th=100
                ti=Image.new("RGBA",(tw,th),(0,0,0,0)); td=ImageDraw.Draw(ti)
                td.text((4,4),text,font=tf,fill="white",stroke_width=2,stroke_fill="black")
                tb=ti.getbbox(); ti=ti.crop(tb) if tb else ti
                buf=io.BytesIO(); ti.save(buf,format="PNG"); tsrc="data:image/png;base64,"+base64.b64encode(buf.getvalue()).decode("ascii")
                tp=lp["text"]
                drawing["objects"].append({"type":"image","version":"4.4.0","name":"overlay_text","left":int(frame.width*tp["x"]/100),"top":int(frame.height*tp["y"]/100),"width":ti.width,"height":ti.height,"scaleX":tp["scale"],"scaleY":tp["scale"],"src":tsrc,"crossOrigin":"anonymous"})
            if logo:
                raw=logo.getvalue()
                try:
                    lim=Image.open(io.BytesIO(raw)).convert("RGBA")
                    lim.thumbnail((max(1,int(frame.width*0.18)),max(1,int(frame.height*0.35))))
                    ratio=lim.height/max(1,lim.width)
                    lbuf=io.BytesIO(); lim.save(lbuf,format="PNG")
                    src="data:image/png;base64,"+base64.b64encode(lbuf.getvalue()).decode("ascii")
                except Exception:
                    lim=Image.new("RGBA",(120,80),(0,0,0,0)); ratio=1
                    lbuf=io.BytesIO(); lim.save(lbuf,format="PNG"); src="data:image/png;base64,"+base64.b64encode(lbuf.getvalue()).decode("ascii")
                # Use the exact normalized PNG dimensions so Fabric does not stretch or black-box the logo.
                drawing["objects"].append({
                    "type":"image","version":"4.4.0","name":"overlay_logo","left":int(frame.width*lp["logo"]["x"]/100),
                    "top":int(frame.height*lp["logo"]["y"]/100),"width":lim.width,
                    "height":lim.height,"scaleX":lp["logo"]["scale"],"scaleY":lp["logo"]["scale"],"opacity":1,
                    "src":src,"crossOrigin":"anonymous"
                })
            st.markdown("**👆 Preview ပေါ်က Blur/Subtitle/စာသား/Logo ကို တိုက်ရိုက်နှိပ်ပြီး ဖိဆွဲပါ။ ထောင့်ကိုဆွဲရင် အရွယ်အစားပြောင်းနိုင်ပါတယ်။ Adjust slider မလိုပါ။**")
            canvas_result=st_canvas(
                fill_color="rgba(255, 255, 255, 0.15)",stroke_width=1,
                background_image=frame,background_color="#222222",
                update_streamlit=True,width=frame.width,height=frame.height,
                initial_drawing=drawing,
                key="live_edit_drag_canvas"
            )
            # Persist direct manipulation geometry so it is used in final rendering too.
            objects=(canvas_result.json_data or {}).get("objects",[]) if canvas_result else []
            for obj in objects:
                name=obj.get("name")
                if name not in ("overlay_blur","overlay_subtitle","overlay_text","overlay_logo"):
                    continue
                x=max(0,min(99,round(float(obj.get("left",0))/frame.width*100)))
                y=max(0,min(99,round(float(obj.get("top",0))/frame.height*100)))
                scale=max(0.2,min(5.0,float(obj.get("scaleX",1))))
                if name=="overlay_blur":
                    lp["blur"].update({"x":x,"y":y,
                        "w":max(1,min(100-x,round(float(obj.get("width",1))*float(obj.get("scaleX",1))/frame.width*100))),
                        "h":max(1,min(100-y,round(float(obj.get("height",1))*float(obj.get("scaleY",1))/frame.height*100)))})
                elif name=="overlay_subtitle": lp["subtitle"].update({"x":x,"y":y,"scale":scale})
                elif name=="overlay_text": lp["text"].update({"x":x,"y":y,"scale":scale})
                elif name=="overlay_logo": lp["logo"].update({"x":x,"y":y,"scale":scale})
            st.session_state.live_positions=lp
            blur_x,blur_y,blur_w,blur_h=lp["blur"]["x"],lp["blur"]["y"],lp["blur"]["w"],lp["blur"]["h"]
            tx,ty=lp["text"]["x"],lp["text"]["y"]
            logo_x,logo_y=lp["logo"]["x"],lp["logo"]["y"]
            subtitle_y=lp["subtitle"]["y"]
            pos="အပေါ်" if subtitle_y < 50 else "အောက်"
            st.caption("Canvas မှာ ဆွဲရွှေ့/အရွယ်အစားပြောင်းပြီးနောက် Final MP4 Render လုပ်ပါ။")
            st.caption(f"လက်ရှိ Effect: {'Blur' if blur else ('စာသား' if text else ('Logo' if logo else 'မရွေးရသေး'))} · Subtitle: {'ON' if burn else 'OFF'} · Mirror: {'ON' if mirror else 'OFF'}")
        except Exception as e:
            st.warning(f"Live Edit မဖွင့်နိုင်သေးပါ: {e}")
    else:
        st.info("Live Edit လုပ်ရန် Video ကို အရင် Upload လုပ်ပါ။")

    if st.button("🎬 Final MP4 Render",disabled=not st.session_state.video_bytes or not st.session_state.voice_bytes):
        with st.spinner("FFmpeg နဲ့ Render လုပ်နေပါတယ်..."):
            try:
                with tempfile.TemporaryDirectory() as td:
                    root=Path(td); ext=Path(st.session_state.video_name or "video.mp4").suffix or ".mp4"
                    vp=root/("input"+ext); ap=root/"voice.wav"; op=root/"final.mp4"
                    vp.write_bytes(st.session_state.video_bytes); ap.write_bytes(st.session_state.voice_bytes)
                    filters=[]
                    if mirror: filters.append("hflip")
                    if burn and st.session_state.burmese_srt.strip():
                        sp=root/"burmese.srt"; sp.write_text(st.session_state.burmese_srt,encoding="utf-8-sig")
                        align=2 if pos=="အောက်" else 8
                        margin_v=max(0,int((100-subtitle_y if pos=="အောက်" else subtitle_y)*720/100))
                        filters.append(f"subtitles='{esc(sp)}':charenc=UTF-8:force_style='FontName=Noto Sans Myanmar,FontSize={max(8,int(fontsize*st.session_state.live_positions['subtitle']['scale']))},Outline=2,Shadow=1,Alignment={align},MarginV={margin_v}'")
                    fp=font()
                    if text.strip() and fp:
                        safe=text.replace("\\","\\\\").replace(":","\\:").replace("'","\\'").replace("%","\\%")
                        filters.append(f"drawtext=fontfile='{esc(fp)}':text='{safe}':fontcolor=white:fontsize={max(8,int(36*lp['text']['scale']))}:borderw=3:bordercolor=black:x=w*{tx/100:.3f}:y=h*{ty/100:.3f}")
                    args=["ffmpeg","-y","-i",str(vp),"-i",str(ap)]
                    graph=[]; vf=",".join(filters) if filters else "null"
                    if blur:
                        bx=blur_x/100; by=blur_y/100; bw=min(blur_w/100,1-bx); bh=min(blur_h/100,1-by)
                        graph.append(f"[0:v]{vf}[clean];[clean]split[base][tmp];[tmp]crop=w=iw*{bw:.4f}:h=ih*{bh:.4f}:x=iw*{bx:.4f}:y=ih*{by:.4f},boxblur={blur}:1[blurred];[base][blurred]overlay=x=W*{bx:.4f}:y=H*{by:.4f}[blurout]")
                        video_base="[blurout]"
                    else:
                        graph.append(f"[0:v]{vf}[base]"); video_base="[base]"
                    if logo:
                        lp=root/("logo"+(Path(logo.name).suffix or ".png")); lp.write_bytes(logo.getvalue())
                        args += ["-i",str(lp)]
                        graph.append(f"[2:v]scale=iw*{0.18*st.session_state.live_positions['logo']['scale']:.4f}:-1[lg];{video_base}[lg]overlay=W*{logo_x/100:.3f}:H*{logo_y/100:.3f}[vout]")
                    else: graph.append(f"{video_base}null[vout]")
                    if mix: graph.append(f"[0:a]volume={vol/100:.2f}[bg];[bg][1:a]amix=inputs=2:duration=first:dropout_transition=2[aout]")
                    args += ["-filter_complex",";".join(graph),"-map","[vout]"]
                    args += ["-map","[aout]"] if mix else ["-map","1:a:0"]
                    args += ["-c:v","libx264","-preset","ultrafast","-crf","23","-c:a","aac","-b:a","192k","-shortest","-movflags","+faststart",str(op)]
                    cmd(args); st.session_state.final_bytes=op.read_bytes()
                    tp=root/"thumbnail.jpg"
                    ta=["ffmpeg","-y","-ss","1","-i",str(op),"-frames:v","1","-vf","scale=1280:-2"]
                    if text.strip() and fp:
                        safe=text.replace("\\","\\\\").replace(":","\\:").replace("'","\\'").replace("%","\\%")
                        ta[-1]+=f",drawtext=fontfile='{esc(fp)}':text='{safe}':fontcolor=white:fontsize=52:borderw=4:bordercolor=black:x=(w-text_w)/2:y=h-text_h-50"
                    ta.append(str(tp))
                    try: cmd(ta); st.session_state.thumbnail_bytes=tp.read_bytes()
                    except Exception: st.session_state.thumbnail_bytes=None
                st.success("Final Video ပြီးပါပြီ။")
            except Exception as e: st.error(f"Render error: {e}")
    if st.session_state.final_bytes:
        st.video(st.session_state.final_bytes)
        st.download_button("⬇️ Final MP4 Download",st.session_state.final_bytes,f"{st.session_state.project_name}.mp4","video/mp4")
    if st.session_state.thumbnail_bytes:
        st.subheader("Thumbnail"); st.image(st.session_state.thumbnail_bytes)
        st.download_button("⬇️ Thumbnail Download",st.session_state.thumbnail_bytes,f"{st.session_state.project_name}_thumbnail.jpg","image/jpeg")
    st.caption("Preview frame ကို cache လုပ်ထားပါတယ်။ Blur ကို ရွေးပြီး X/Y နဲ့ အကျယ်/အမြင့်ကို ချိန်ပါ — ရွေးထားတဲ့ ဧရိယာပဲ ဝါးစေပါတယ်။")

st.divider()
st.caption("အရေးကြီး SRT၊ Voice၊ Final MP4 ကို အလုပ်ပြီးတိုင်း Download လုပ်ထားပါ။ Session ပြတ်လျှင် မသိမ်းရသေးသော data ပျောက်နိုင်သည်။")
