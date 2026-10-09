# Lynn Recap Streamlit Studio

Mobile-friendly Streamlit app for a four-step Myanmar movie recap workflow.

## Features
- Upload movie/video and extract timed original SRT with Groq Whisper (`whisper-large-v3-turbo`).
- Translate subtitles into natural Myanmar Burmese with Gemini.
- Generate Myanmar narration with Gemini text-to-speech.
- Live edit preview with draggable/resizable blur region, subtitle, logo and text overlays.
- Mirror video, adjust blur, subtitle size and original-audio mix, then export MP4 with FFmpeg.
- Generate and download a thumbnail and the final MP4.
- Session-only API key inputs; no key is saved into source files.

## API keys
- Groq key (for fast SRT): https://console.groq.com/keys
- Gemini key (translation and AI voice): https://aistudio.google.com/apikey

## Streamlit Community Cloud
1. Open https://share.streamlit.io/ and select this repository and branch `main`.
2. Set the main file path to `app.py`.
3. Streamlit installs `requirements.txt` and system packages from `packages.txt`.

## Render
Connect this repository to Render as a Docker Web Service using `render.yaml`. The Docker image installs Python, Streamlit, FFmpeg and Noto fonts.

## Notes
- Streamlit Cloud has resource and request-size limits; 900 MB videos may exceed available memory or upload limits even though the app requests a 900 MB maximum. Render capacity and disk limits also depend on the selected plan.
- Render free filesystem is ephemeral. Download SRT, voice, MP4 and thumbnail when each step completes.
- Review generated transcripts and translations before publishing. Only process video you own or are authorized to use.
