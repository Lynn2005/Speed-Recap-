# Speed Recap Studio

Mobile-friendly video recap workspace built with Node.js, Express, FFmpeg/FFprobe and Gemini API support.

## Features in this starter
- Upload large video files (configurable; default 900 MB) to the server.
- Four-step workflow: Upload, Script, Voice, Final Export.
- Gemini-powered Myanmar recap script generation from a supplied transcript.
- Gemini text-to-speech endpoint with WAV output.
- Browser live editor with draggable/resizable subtitle, logo, text and blur overlays.
- FFmpeg export with aspect-ratio presets, mirror, blur and subtitle burn-in.
- Job status endpoints and server-side file handling.
- Docker + Render Blueprint configuration.

## Requirements
- Node.js 20+ and FFmpeg/FFprobe, or Docker.
- A Gemini API key. You can obtain one at https://aistudio.google.com/apikey.
- Upload only videos you own or are authorized to process.

## Local run
```bash
npm install
cp .env.example .env
npm start
```
Set `GEMINI_API_KEY` in `.env` for server-side Gemini requests, or enter a key in the UI for the current session. UI-entered keys are not saved in browser storage.

## Render
The repository includes `render.yaml` and a Dockerfile. Connect the repository to Render as a Docker Web Service. For persistent uploads and exports, configure a persistent disk mounted at `/var/data`; add `GEMINI_API_KEY` as a secret environment variable. Render deploys from the connected branch according to the service's auto-deploy settings.

## Important scope notes
Video upload, editor controls, FFmpeg export, Gemini recap generation and Gemini TTS are implemented as separate stages. Accurate automatic timestamped ASR from arbitrary uploaded movies is not claimed by this initial build; use the transcript input stage or add a dedicated speech-to-text provider. Export duration depends on the instance's CPU, available disk and input video size.
