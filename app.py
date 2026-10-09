import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import requests
import streamlit as st
from PIL import Image

st.set_page_config(page_title="Lynn Recap", page_icon="🎬", layout="wide")
st.markdown("""
<style>
.block-container{max-width:1250px;padding-top:1.2rem}
.stButton>button,.stDownloadButton>button{width:100%;border-radius:10px;min-height:2.6rem}
[data-testid="stSidebar"]{background:#101a2c}
</style>
""", unsafe_allow_html=True)
st.title("🎬 Lynn Recap")
st.caption("Movie → မြန်မာ SRT → Gemini AI Voice → Edit → Final MP4")

DEFAULTS = {
    "video_path": "", "video_name": "", "original_srt": "", "burmese_srt": "",
    "voice_path": "", "final_path": "", "thumb_path": "", "project_name": "lynn_recap",
    "saved_groq_key": "", "saved_gemini_key": ""
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value

def run_cmd(args, timeout=None):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Command failed")[-3500:])
    return result.stdout.strip()

def ffmpeg_ready():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        st.error("FFmpeg မတွေ့ပါ။ Render Dockerfile ထဲတွင် ffmpeg install လုပ်ထားကြောင်း စစ်ပါ။")
        return False
    return True

def srt_time(seconds):
    total = max(0, int(float(seconds) * 1000))
    h, rem = divmod(total, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def clean_srt(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n\s*\n", text)
    out = []
    for i, block in enumerate(blocks, 1):
        lines = [x.strip().lstrip("\ufeff") for x in block.split("\n") if x.strip()]
        if not lines:
            continue
        time_line = next((x for x in lines if "-->" in x), None)
        if not time_line:
            continue
        idx = lines.index(time_line)
        caption = [x for x in lines[idx+1:] if not x.isdigit()]
        if caption:
            out.append(f"{len(out)+1}\n{time_line}\n" + "\n".join(caption))
    if not out:
        raise ValueError("SRT format မမှန်ပါ။ 00:00:01,000 --> 00:00:03,000 ပုံစံဖြစ်ရပါမယ်။")
    return "\n\n".join(out) + "\n"

def call_gemini(api_key, parts, model="gemini-2.5-flash"):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    response = requests.post(url, params={"key": api_key}, json={"contents":[{"parts":parts}]}, timeout=180)
    if not response.ok:
        raise RuntimeError(f"Gemini API {response.status_code}: {response.text[:1200]}")
    data = response.json()
    try:
        return "".join(p.get("text","") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Gemini က စာသားမပြန်လာပါ။ API key/model/quota ကိုစစ်ပါ။")

def save_upload(uploaded, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        while True:
            chunk = uploaded.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    uploaded.seek(0)

def transcribe_video(video_path, api_key, workdir):
    audio_pattern = str(workdir / "audio_%03d.mp3")
    run_cmd(["ffmpeg","-y","-i",str(video_path),"-vn","-ac","1","-ar","16000","-b:a","48k","-f","segment","-segment_time","480","-reset_timestamps","1",audio_pattern], timeout=3600)
    parts = sorted(workdir.glob("audio_*.mp3"))
    if not parts:
        raise RuntimeError("ဗီဒီယိုထဲတွင် အသံမတွေ့ပါ။ အသံပါသော video ကိုတင်ပါ။")
    all_segments = []
    offset = 0.0
    for part in parts:
        with open(part, "rb") as f:
            res = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization":"Bearer " + api_key},
                data={"model":"whisper-large-v3-turbo","response_format":"verbose_json","temperature":"0"},
                files={"file":(part.name,f,"audio/mpeg")},
                timeout=900
            )
        if not res.ok:
            raise RuntimeError(f"Groq API {res.status_code}: {res.text[:1200]}")
        data = res.json()
        for seg in data.get("segments", []):
            start = float(seg.get("start",0)) + offset
            end = float(seg.get("end",start)) + offset
            caption = str(seg.get("text","")).strip()
            if caption:
                all_segments.append((start,end,caption))
        duration = float(data.get("duration",0) or 0)
        if duration <= 0:
            probe = run_cmd(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(part)])
            duration = float(probe or 0)
        offset += duration
    if not all_segments:
        raise RuntimeError("အသံမှ စာသားမထုတ်နိုင်ပါ။ အခြား video သို့မဟုတ် API key ကိုစစ်ပါ။")
    return "\n\n".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}" for i,(a,b,t) in enumerate(all_segments,1)) + "\n"

def gemini_voice(text, api_key, output_path):
    # Generate in chunks to avoid oversized TTS requests.
    chunks = [text[i:i+1800] for i in range(0, len(text), 1800)] or [text]
    pcm_all = bytearray()
    for chunk in chunks:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent"
        payload = {
            "contents":[{"parts":[{"text":"Read naturally in a clear, warm Myanmar Burmese narrator voice. Speak only the following text, without adding anything:\n" + chunk}]}],
            "generationConfig":{"responseModalities":["AUDIO"],"speechConfig":{"voiceConfig":{"prebuiltVoiceConfig":{"voiceName":"Kore"}}}}
        }
        res = requests.post(url, params={"key":api_key}, json=payload, timeout=240)
        if not res.ok:
            raise RuntimeError(f"Gemini TTS API {res.status_code}: {res.text[:1200]}")
        data = res.json()
        try:
            inline = next(p["inlineData"] for p in data["candidates"][0]["content"]["parts"] if "inlineData" in p)
            raw = base64.b64decode(inline["data"])
            mime = inline.get("mimeType","audio/L16;rate=24000")
            rate_match = re.search(r"rate=(\d+)", mime)
            sample_rate = int(rate_match.group(1)) if rate_match else 24000
            # Gemini returns raw signed 16-bit PCM for audio/L16.
            if "wav" in mime.lower():
                with wave.open(io.BytesIO(raw), "rb") as wf:
                    pcm_all.extend(wf.readframes(wf.getnframes()))
                    sample_rate = wf.getframerate()
            else:
                pcm_all.extend(raw)
        except (KeyError, StopIteration, IndexError, TypeError) as exc:
            raise RuntimeError("Gemini TTS အသံ data မရပါ။ Model ရရှိမှုနှင့် API quota ကိုစစ်ပါ။") from exc
    if not pcm_all:
        raise RuntimeError("AI voice အလွတ်ဖြစ်နေပါသည်။")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(pcm_all))
    return output_path

def escape_filter_path(path):
    return str(path).replace("\\","/").replace(":","\\:").replace("'","\\'").replace("[","\\[").replace("]","\\]")

def render_video(video_path, output_path, srt_text, voice_path=None, mirror=False, blur=0, subtitle_size=24, overlay_text="", logo_path=None, audio_mix=25):
    workdir = output_path.parent
    srt_path = workdir / "captions.srt"
    srt_path.write_text(clean_srt(srt_text), encoding="utf-8")
    filters = []
    if mirror:
        filters.append("hflip")
    if blur > 0:
        filters.append(f"boxblur={int(blur)}:1")
    # Subtitles are drawn after image effects.
    filters.append(f"subtitles='{escape_filter_path(srt_path)}':force_style='FontName=Noto Sans Myanmar,FontSize={int(subtitle_size)},Outline=2,Shadow=1,Alignment=2,MarginV=35'")
    if overlay_text.strip():
        txt = escape_filter_path(workdir / "overlay.txt")
        (workdir / "overlay.txt").write_text(overlay_text.replace("\n"," "), encoding="utf-8")
        filters.append(f"drawtext=textfile='{txt}':font='Noto Sans Myanmar':fontcolor=white:fontsize=28:borderw=2:bordercolor=black:x=(w-text_w)/2:y=40")
    vf = ",".join(filters)
    if logo_path and Path(logo_path).exists():
        cmd = ["ffmpeg","-y","-i",str(video_path),"-i",str(logo_path)]
        if voice_path and Path(voice_path).exists():
            cmd += ["-i",str(voice_path)]
        cmd += ["-filter_complex",f"[0:v]{vf}[v0];[1:v]scale=iw*0.18:-1[logo];[v0][logo]overlay=W-w-24:24[vout]",
                "-map","[vout]"]
        if voice_path and Path(voice_path).exists():
            cmd += ["-filter_complex",f"[0:v]{vf}[v0];[1:v]scale=iw*0.18:-1[logo];[v0][logo]overlay=W-w-24:24[vout];[2:a]volume=1[voice];[0:a]volume={max(0,min(100,audio_mix))/100}[orig];[orig][voice]amix=inputs=2:duration=first[aout]","-map","[aout]"]
        else:
            cmd += ["-map","0:a?"]
    else:
        cmd = ["ffmpeg","-y","-i",str(video_path)]
        if voice_path and Path(voice_path).exists():
            cmd += ["-i",str(voice_path)]
        cmd += ["-vf",vf]
        if voice_path and Path(voice_path).exists():
            cmd += ["-filter_complex",f"[0:a]volume={max(0,min(100,audio_mix))/100}[orig];[1:a]volume=1[voice];[orig][voice]amix=inputs=2:duration=first[aout]","-map","0:v","-map","[aout]"]
        else:
            cmd += ["-map","0:v","-map","0:a?"]
    cmd += ["-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-b:a","160k","-shortest","-movflags","+faststart",str(output_path)]
    run_cmd(cmd, timeout=7200)
    return output_path

with st.sidebar:
    st.subheader("🔑 API Keys")
    with st.form("api_keys_form"):
        groq_in = st.text_input("Groq API Key (SRT)", value=st.session_state.saved_groq_key, type="password")
        gemini_in = st.text_input("Gemini API Key (ဘာသာပြန်/Voice)", value=st.session_state.saved_gemini_key, type="password")
        if st.form_submit_button("API Keys သိမ်းမယ်"):
            st.session_state.saved_groq_key = groq_in.strip()
            st.session_state.saved_gemini_key = gemini_in.strip()
            st.success("လက်ရှိ session အတွက် သိမ်းပြီးပါပြီ။")
    st.markdown("[Groq key ယူရန်](https://console.groq.com/keys)  ·  [Gemini key ယူရန်](https://aistudio.google.com/apikey)")
    st.divider()
    st.session_state.project_name = st.text_input("Output ဖိုင်နာမည်", value=st.session_state.project_name).strip() or "lynn_recap"
    if st.button("🧹 New Project / အစမှပြန်စမယ်"):
        for k,v in DEFAULTS.items():
            st.session_state[k] = v
        st.rerun()

tab1, tab2, tab3, tab4 = st.tabs(["1. Upload Video","2. Transcript & Translate","3. AI Voice","4. Final Video & Thumbnail"])

with tab1:
    st.header("ဗီဒီယိုတင်ရန်")
    uploaded = st.file_uploader("Movie/video file (MP4, MOV, MKV)", type=["mp4","mov","mkv","webm","avi"])
    if uploaded:
        if uploaded.size > 900 * 1024 * 1024:
            st.error("ဖိုင် 900 MB ထက်ကြီးနေပါတယ်။ ပိုသေးတဲ့ဖိုင်ကိုသုံးပါ။")
        elif st.button("⬆️ Upload သိမ်းမယ်", key="save_upload"):
            if not ffmpeg_ready():
                st.stop()
            base = Path(tempfile.gettempdir()) / "lynn_recap_session"
            base.mkdir(parents=True, exist_ok=True)
            dest = base / re.sub(r"[^A-Za-z0-9._-]", "_", uploaded.name)
            try:
                save_upload(uploaded, dest)
                st.session_state.video_path = str(dest)
                st.session_state.video_name = uploaded.name
                st.session_state.final_path = ""
                st.session_state.thumb_path = ""
                st.success("Video သိမ်းပြီးပါပြီ။ နောက်တစ်ဆင့်သို့ သွားပါ။")
            except Exception as e:
                st.error(f"Upload မအောင်မြင်ပါ: {e}")
    if st.session_state.video_path and Path(st.session_state.video_path).exists():
        st.success("လက်ရှိ Video: " + st.session_state.video_name)
        st.video(st.session_state.video_path)

with tab2:
    st.header("SRT ထုတ်ပြီး မြန်မာဘာသာပြန်ရန်")
    st.info("SRT ထုတ်ရန် Groq key၊ ဘာသာပြန်ရန် Gemini key လိုအပ်ပါသည်။")
    if st.button("⚡ 1. Auto SRT ထုတ်မယ်", key="transcribe"):
        if not st.session_state.video_path or not Path(st.session_state.video_path).exists():
            st.error("ပထမဆုံး Upload Video အဆင့်တွင် ဗီဒီယိုတင်ပါ။")
        elif not st.session_state.saved_groq_key:
            st.error("Sidebar တွင် Groq API key ထည့်ပြီး သိမ်းပါ။")
        elif not ffmpeg_ready():
            st.stop()
        else:
            try:
                with st.spinner("အသံခွဲပြီး SRT ထုတ်နေပါတယ်။ Video ကြာလျှင် အချိန်ယူနိုင်ပါတယ်..."):
                    wd = Path(tempfile.gettempdir()) / "lynn_recap_session"
                    st.session_state.original_srt = transcribe_video(Path(st.session_state.video_path), st.session_state.saved_groq_key, wd)
                st.success("SRT ထုတ်ပြီးပါပြီ။")
            except Exception as e:
                st.error(f"SRT error: {e}")
    st.session_state.original_srt = st.text_area("Original SRT (လိုပါက ပြင်နိုင်သည်)", value=st.session_state.original_srt, height=220, key="original_editor")
    if st.button("🌐 2. မြန်မာဘာသာပြန်မယ်", key="translate"):
        if not st.session_state.saved_gemini_key:
            st.error("Sidebar တွင် Gemini API key ထည့်ပြီး သိမ်းပါ။")
        elif not st.session_state.original_srt.strip():
            st.error("Original SRT မရှိသေးပါ။")
        else:
            try:
                with st.spinner("မြန်မာဘာသာပြန်နေပါတယ်..."):
                    src = clean_srt(st.session_state.original_srt)
                    translated = call_gemini(st.session_state.saved_gemini_key, [{"text":"Translate the subtitle text in this SRT into natural conversational Myanmar Burmese. Keep every subtitle index and timestamp exactly unchanged. Return ONLY valid SRT, no markdown fences.\n\n"+src}])
                    st.session_state.burmese_srt = clean_srt(translated)
                st.success("မြန်မာဘာသာပြန်ပြီးပါပြီ။")
            except Exception as e:
                st.error(f"ဘာသာပြန် error: {e}")
    st.session_state.burmese_srt = st.text_area("မြန်မာ SRT (အသံထွက်စာသားအပါအဝင် ပြင်နိုင်သည်)", value=st.session_state.burmese_srt, height=260, key="burmese_editor")
    st.download_button("⬇️ မြန်မာ SRT Download", data=st.session_state.burmese_srt or "", file_name="myanmar_subtitles.srt", mime="application/x-subrip", disabled=not bool(st.session_state.burmese_srt.strip()))

with tab3:
    st.header("Gemini AI Voice")
    st.caption("မြန်မာ narration စာသားကို SRT ထဲကနေ ယူပြီး အသံဖန်တီးပါမယ်။")
    voice_text = st.text_area("AI Voice ပြောမည့်စာသား", value="\n".join([x for x in re.sub(r"(?m)^\d+\s*$|^.*-->.*$","",st.session_state.burmese_srt).splitlines() if x.strip()]), height=220, key="voice_text")
    if st.button("🎙️ Gemini AI Voice ထုတ်မယ်", key="make_voice"):
        if not st.session_state.saved_gemini_key:
            st.error("Sidebar တွင် Gemini API key ထည့်ပြီး သိမ်းပါ။")
        elif not voice_text.strip():
            st.error("အသံဖန်တီးရန် စာသားထည့်ပါ။")
        else:
            try:
                with st.spinner("Gemini AI voice ဖန်တီးနေပါတယ်..."):
                    voice_out = Path(tempfile.gettempdir()) / "lynn_recap_session" / "voice.wav"
                    gemini_voice(voice_text.strip(), st.session_state.saved_gemini_key, voice_out)
                    st.session_state.voice_path = str(voice_out)
                st.success("AI voice ထုတ်ပြီးပါပြီ။")
            except Exception as e:
                st.error(f"AI Voice error: {e}")
    if st.session_state.voice_path and Path(st.session_state.voice_path).exists():
        st.audio(st.session_state.voice_path)
        st.download_button("⬇️ Voice WAV Download", data=Path(st.session_state.voice_path).read_bytes(), file_name="myanmar_voice.wav", mime="audio/wav")

with tab4:
    st.header("Live Edit Preview & Final Export")
    if not st.session_state.video_path or not Path(st.session_state.video_path).exists():
        st.warning("အရင်ဆုံး Upload Video အဆင့်တွင် video တင်ပါ။")
    else:
        left, right = st.columns([1,1])
        with left:
            st.subheader("Preview")
            st.video(st.session_state.video_path)
        with right:
            st.subheader("Edit Settings")
            mirror = st.checkbox("Mirror / ဘယ်ညာပြောင်း", value=False)
            blur = st.slider("Blur strength (0 = မမှုန်)", 0, 10, 0)
            subtitle_size = st.slider("Subtitle size", 14, 42, 24)
            overlay_text = st.text_input("Logo မဟုတ်သော စာသား", value="")
            logo_file = st.file_uploader("Logo ပုံ (PNG/JPG)", type=["png","jpg","jpeg"], key="logo_upload")
            audio_mix = st.slider("Original audio volume (%)", 0, 100, 25)
            srt_for_video = st.text_area("Final video subtitle SRT", value=st.session_state.burmese_srt, height=180, key="final_srt")
        if st.button("🎬 Final MP4 ထုတ်မယ်", key="export"):
            if not srt_for_video.strip():
                st.error("Final video အတွက် SRT ထည့်ပါ။")
            elif not ffmpeg_ready():
                st.stop()
            else:
                try:
                    with st.spinner("Video render လုပ်နေပါတယ်။ ဖိုင်ကြီးလျှင် အချိန်ယူနိုင်ပါတယ်..."):
                        outdir = Path(tempfile.gettempdir()) / "lynn_recap_session"
                        outdir.mkdir(parents=True, exist_ok=True)
                        logo_path = None
                        if logo_file:
                            logo_path = outdir / "logo_image" + Path(logo_file.name).suffix
                            logo_path.write_bytes(logo_file.getvalue())
                        output = outdir / (re.sub(r"[^A-Za-z0-9_-]","_",st.session_state.project_name) + "_final.mp4")
                        render_video(st.session_state.video_path, output, srt_for_video, st.session_state.voice_path or None, mirror, blur, subtitle_size, overlay_text, logo_path, audio_mix)
                        st.session_state.final_path = str(output)
                    st.success("Final video ပြီးပါပြီ။")
                except Exception as e:
                    st.error(f"Video export error: {e}")
        if st.session_state.final_path and Path(st.session_state.final_path).exists():
            st.video(st.session_state.final_path)
            st.download_button("⬇️ Final MP4 Download", data=Path(st.session_state.final_path).read_bytes(), file_name=Path(st.session_state.final_path).name, mime="video/mp4")
        if st.button("🖼️ Thumbnail ဖန်တီးမယ်", key="make_thumbnail"):
            try:
                thumbdir = Path(tempfile.gettempdir()) / "lynn_recap_session"
                thumbdir.mkdir(parents=True, exist_ok=True)
                thumb = thumbdir / "thumbnail.jpg"
                run_cmd(["ffmpeg","-y","-ss","00:00:03","-i",st.session_state.video_path,"-frames:v","1","-q:v","2",str(thumb)], timeout=120)
                st.session_state.thumb_path = str(thumb)
            except Exception as e:
                st.error(f"Thumbnail error: {e}")
        if st.session_state.thumb_path and Path(st.session_state.thumb_path).exists():
            st.image(st.session_state.thumb_path, caption="Thumbnail Preview", use_container_width=True)
            st.download_button("⬇️ Thumbnail Download", data=Path(st.session_state.thumb_path).read_bytes(), file_name="thumbnail.jpg", mime="image/jpeg")

st.divider()
st.caption("Lynn Recap • Streamlit Edition • API keys are stored only in the active session. Render/Streamlit free instances may restart or run out of memory with large videos.")
