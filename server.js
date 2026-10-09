const express = require('express');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('child_process');
const { randomUUID } = require('crypto');

const app = express();
const PORT = Number(process.env.PORT || 10000);
const DATA_DIR = process.env.DATA_DIR || (fs.existsSync('/var/data') ? '/var/data/speed-recap' : path.join(os.tmpdir(), 'speed-recap'));
const UPLOAD_DIR = path.join(DATA_DIR, 'uploads');
const OUTPUT_DIR = path.join(DATA_DIR, 'outputs');
fs.mkdirSync(UPLOAD_DIR, { recursive: true });
fs.mkdirSync(OUTPUT_DIR, { recursive: true });

const MAX_UPLOAD_BYTES = Number(process.env.MAX_UPLOAD_MB || 900) * 1024 * 1024;
const uploads = new Map();
const jobs = new Map();
const voiceOutputs = new Map();
const UPLOAD_INDEX = path.join(DATA_DIR, 'uploads.json');
try {
  const saved = JSON.parse(fs.readFileSync(UPLOAD_INDEX, 'utf8'));
  for (const item of saved) if (item && item.id && item.path && fs.existsSync(item.path)) uploads.set(item.id, item);
} catch {}

app.use(express.json({ limit: '10mb' }));
app.use(express.static(path.join(__dirname, 'public')));
app.get('/api/health', (_req, res) => res.json({ ok: true, app: 'Speed Recap', ffmpeg: commandExistsHint(), maxUploadMB: Math.floor(MAX_UPLOAD_BYTES / 1024 / 1024) }));

function commandExistsHint() { return 'server-side'; }
function run(command, args, timeoutMs = 30 * 60 * 1000) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = ''; let stderr = ''; let settled = false;
    const timer = setTimeout(() => { child.kill('SIGKILL'); if (!settled) { settled = true; reject(new Error(command + ' timed out')); } }, timeoutMs);
    child.stdout.on('data', d => { stdout += d.toString(); if (stdout.length > 200000) stdout = stdout.slice(-200000); });
    child.stderr.on('data', d => { stderr += d.toString(); if (stderr.length > 200000) stderr = stderr.slice(-200000); });
    child.on('error', err => { clearTimeout(timer); if (!settled) { settled = true; reject(err); } });
    child.on('close', code => { clearTimeout(timer); if (settled) return; settled = true; code === 0 ? resolve({ stdout, stderr }) : reject(new Error(stderr.slice(-5000) || command + ' exited with code ' + code)); });
  });
}
function safeError(err) { return String(err && err.message || err).slice(0, 1200); }
function getKey(req) { return String(req.body?.apiKey || req.headers['x-gemini-api-key'] || process.env.GEMINI_API_KEY || '').trim(); }
async function geminiGenerate(apiKey, model, contents, generationConfig) {
  const url = 'https://generativelanguage.googleapis.com/v1beta/models/' + encodeURIComponent(model) + ':generateContent?key=' + encodeURIComponent(apiKey);
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contents, ...(generationConfig ? { generationConfig } : {}) })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || 'Gemini request failed (' + response.status + ')');
  return data;
}
function pcmToWav(pcm, sampleRate = 24000, channels = 1, bits = 16) {
  const data = Buffer.from(pcm);
  const header = Buffer.alloc(44);
  header.write('RIFF', 0); header.writeUInt32LE(36 + data.length, 4); header.write('WAVE', 8);
  header.write('fmt ', 12); header.writeUInt32LE(16, 16); header.writeUInt16LE(1, 20);
  header.writeUInt16LE(channels, 22); header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * channels * bits / 8, 28);
  header.writeUInt16LE(channels * bits / 8, 32); header.writeUInt16LE(bits, 34);
  header.write('data', 36); header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

const storage = multer.diskStorage({
  destination: (_req, _file, cb) => cb(null, UPLOAD_DIR),
  filename: (_req, file, cb) => cb(null, randomUUID() + path.extname(file.originalname).toLowerCase().replace(/[^.a-z0-9]/g, '').slice(0, 12))
});
const upload = multer({
  storage,
  limits: { fileSize: MAX_UPLOAD_BYTES, files: 1 },
  fileFilter: (_req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    const allowed = ['.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi', '.mpeg', '.mpg'];
    cb(allowed.includes(ext) ? null : new Error('Unsupported video format. Use MP4, MOV, MKV, WEBM, M4V, AVI or MPEG.'), allowed.includes(ext));
  }
});

app.post('/api/upload', (req, res) => {
  upload.single('video')(req, res, async err => {
    if (err) return res.status(400).json({ error: safeError(err) });
    if (!req.file) return res.status(400).json({ error: 'Select a video file first.' });
    const id = randomUUID();
    try {
      const { stdout } = await run('ffprobe', ['-v','error','-show_entries','format=duration,size:format_tags=title','-show_entries','stream=codec_type,width,height','-of','json', req.file.path], 60000);
      const meta = JSON.parse(stdout || '{}');
      const item = {
        id, path: req.file.path, originalName: req.file.originalname, size: req.file.size,
        duration: Number(meta.format?.duration || 0),
        width: Number(meta.streams?.find(s => s.codec_type === 'video')?.width || 0),
        height: Number(meta.streams?.find(s => s.codec_type === 'video')?.height || 0),
        createdAt: new Date().toISOString()
      };
      uploads.set(id, item);
      fs.writeFileSync(UPLOAD_INDEX, JSON.stringify([...uploads.values()]));
      res.json({ file: { id, name: item.originalName, size: item.size, duration: item.duration, width: item.width, height: item.height } });
    } catch (e) {
      fs.unlink(req.file.path, () => {});
      res.status(422).json({ error: 'FFprobe could not read this video: ' + safeError(e) });
    }
  });
});

app.post('/api/transcribe', async (req, res) => {
  const file = uploads.get(String(req.body.fileId || ''));
  const apiKey = getKey(req);
  if (!file || !fs.existsSync(file.path)) return res.status(404).json({ error: 'Upload video ကို ပြန်ရွေးပါ။' });
  if (!apiKey) return res.status(400).json({ error: 'Gemini API key ထည့်ပါ။ https://aistudio.google.com/apikey' });
  const id = randomUUID();
  const job = { id, type: 'transcribe', state: 'processing', progress: 2, message: 'Extracting audio chunks', createdAt: new Date().toISOString() };
  jobs.set(id, job);
  res.json({ jobId: id, state: job.state });
  (async () => {
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'speed-recap-asr-'));
    try {
      await run('ffmpeg', ['-y','-i',file.path,'-vn','-ac','1','-ar','16000','-f','segment','-segment_time','120','-reset_timestamps','1','-c:a','libmp3lame','-b:a','48k',path.join(tempDir,'chunk-%03d.mp3')], 60 * 60 * 1000);
      const chunks = fs.readdirSync(tempDir).filter(n => n.endsWith('.mp3')).sort();
      if (!chunks.length) throw new Error('ဒီ video မှာ transcribe လုပ်နိုင်တဲ့ audio မတွေ့ပါ။');
      let transcript = '';
      for (let i = 0; i < chunks.length; i++) {
        job.progress = Math.min(92, 5 + Math.round((i / chunks.length) * 85));
        job.message = 'Transcribing audio chunk ' + (i + 1) + ' / ' + chunks.length;
        const audio = fs.readFileSync(path.join(tempDir, chunks[i])).toString('base64');
        const prompt = 'Transcribe all audible speech in this audio clip as accurately as possible, in the original spoken language. Do not summarize or translate. Return one spoken utterance per line, each with an approximate timestamp relative to the beginning of THIS clip in HH:MM:SS format, like 00:00:04 | words. If no speech is audible, return [No speech]. Do not invent words.';
        const data = await geminiGenerate(apiKey, 'gemini-2.5-flash', [{ role: 'user', parts: [{ text: prompt }, { inlineData: { mimeType: 'audio/mpeg', data: audio } }] }], { temperature: 0.1 });
        let chunkText = (data.candidates?.[0]?.content?.parts || []).map(p => p.text || '').join('\n').trim();
        const offset = i * 120;
        chunkText = chunkText.replace(/\b(\d{2}):(\d{2}):(\d{2})\b/g, (_m,h,m,s) => {
          const total = Number(h) * 3600 + Number(m) * 60 + Number(s) + offset;
          return [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60].map(n => String(n).padStart(2,'0')).join(':');
        });
        if (chunkText && chunkText !== '[No speech]') transcript += (transcript ? '\n' : '') + chunkText;
      }
      if (!transcript.trim()) throw new Error('စကားပြော transcript မတွေ့ပါ။');
      job.state = 'done'; job.progress = 100; job.message = 'Transcription complete'; job.transcript = transcript; job.chunkCount = chunks.length;
    } catch (e) {
      job.state = 'failed'; job.message = safeError(e);
    } finally {
      fs.rm(tempDir, { recursive: true, force: true }, () => {});
    }
  })();
});

app.post('/api/recap', async (req, res) => {
  try {
    const apiKey = getKey(req);
    const transcript = String(req.body.transcript || '').trim();
    if (!apiKey) return res.status(400).json({ error: 'Gemini API key ထည့်ပါ။ https://aistudio.google.com/apikey' });
    if (transcript.length < 20) return res.status(400).json({ error: 'Transcript အနည်းဆုံး စာလုံး ၂၀ ထည့်ပါ။' });
    const lang = req.body.language === 'en' ? 'English' : 'natural spoken Myanmar Burmese';
    const prompt = 'You are an expert short-form movie recap writer. Create an engaging, accurate recap based only on the supplied transcript. Output in ' + lang + '. Preserve character names as written, do not invent plot events, use clear narration, avoid copyrighted dialogue quotations, and end with a concise cliffhanger only if supported. Target about 60-90 seconds of narration. Output only the narration script.\n\nTRANSCRIPT:\n' + transcript.slice(0, 100000);
    const data = await geminiGenerate(apiKey, 'gemini-2.5-flash', [{ role: 'user', parts: [{ text: prompt }] }], { temperature: 0.65 });
    const textOut = (data.candidates?.[0]?.content?.parts || []).map(p => p.text || '').join('\n').trim();
    if (!textOut) throw new Error('Gemini returned no recap text.');
    res.json({ script: textOut, model: 'gemini-2.5-flash' });
  } catch (e) { res.status(502).json({ error: safeError(e) }); }
});

app.post('/api/voice', async (req, res) => {
  try {
    const apiKey = getKey(req);
    const text = String(req.body.text || '').trim();
    if (!apiKey) return res.status(400).json({ error: 'Gemini API key ထည့်ပါ။' });
    if (!text) return res.status(400).json({ error: 'အသံထုတ်မည့်စာသား ထည့်ပါ။' });
    if (text.length > 12000) return res.status(413).json({ error: 'Voice text အရှည်ဆုံး 12,000 characters ဖြစ်ရပါမယ်။' });
    const data = await geminiGenerate(apiKey, 'gemini-2.5-flash-preview-tts', [{
      role: 'user',
      parts: [{ text: 'Read the following narration naturally in a clear, warm Myanmar Burmese voice. Do not translate or add words.\n\n' + text }]
    }], {
      responseModalities: ['AUDIO'],
      speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: 'Kore' } } }
    });
    const part = (data.candidates?.[0]?.content?.parts || []).find(p => p.inlineData?.data);
    if (!part) throw new Error('Gemini TTS returned no audio. Check model availability and API access.');
    const mime = part.inlineData.mimeType || '';
    const raw = Buffer.from(part.inlineData.data, 'base64');
    let wav = raw;
    if (/pcm|L16/i.test(mime)) {
      const rate = Number((mime.match(/rate=(\d+)/i) || [])[1] || 24000);
      wav = pcmToWav(raw, rate, 1, 16);
    }
    const id = randomUUID();
    const outPath = path.join(OUTPUT_DIR, id + '.wav');
    fs.writeFileSync(outPath, wav);
    voiceOutputs.set(id, outPath);
    res.json({ voiceId: id, audioUrl: '/api/output/' + id, mimeType: 'audio/wav', bytes: wav.length });
  } catch (e) { res.status(502).json({ error: safeError(e) }); }
});

app.post('/api/export', async (req, res) => {
  const file = uploads.get(String(req.body.fileId || ''));
  if (!file || !fs.existsSync(file.path)) return res.status(404).json({ error: 'Uploaded video not found. Please upload again after server restart.' });
  const voiceId = String(req.body.voiceId || '');
  const voicePath = voiceOutputs.get(voiceId) || (/^[a-f0-9-]{36}$/i.test(voiceId) ? path.join(OUTPUT_DIR, voiceId + '.wav') : null);
  const id = randomUUID();
  const outputPath = path.join(OUTPUT_DIR, id + '.mp4');
  const job = { id, state: 'processing', progress: 5, message: 'Preparing FFmpeg export', createdAt: new Date().toISOString() };
  jobs.set(id, job);
  res.json({ jobId: id, state: job.state });
  (async () => {
    try {
      const s = req.body.settings || {};
      const aspect = ['9:16','16:9','1:1'].includes(s.aspect) ? s.aspect : '9:16';
      const dimensions = aspect === '9:16' ? { w: 1080, h: 1920 } : aspect === '1:1' ? { w: 1080, h: 1080 } : { w: 1920, h: 1080 };
      const filters = [
        'scale=' + dimensions.w + ':' + dimensions.h + ':force_original_aspect_ratio=increase',
        'crop=' + dimensions.w + ':' + dimensions.h
      ];
      if (s.mirror) filters.push('hflip');
      const overlays = s.overlays || {};
      const clamp = (v, min, max) => Math.max(min, Math.min(max, Number(v) || 0));
      const coords = o => ({
        x: Math.round(clamp(o?.x, 0, 1) * dimensions.w),
        y: Math.round(clamp(o?.y, 0, 1) * dimensions.h),
        w: Math.max(40, Math.round(clamp(o?.w, 0.02, 1) * dimensions.w)),
        h: Math.max(25, Math.round(clamp(o?.h, 0.02, 1) * dimensions.h))
      });
      const blur = overlays.blur && overlays.blur.visible ? coords(overlays.blur) : null;
      const esc = value => String(value || '').replace(/\\/g, '\\\\').replace(/:/g, '\\:').replace(/'/g, "\\'").replace(/,/g, '\\,').replace(/;/g, '\\;').replace(/\[/g, '\\[').replace(/\]/g, '\\]').replace(/%/g, '\\%').replace(/\n/g, ' ');
      const drawtext = (kind, o) => {
        if (!o || !o.visible || !String(o.text || '').trim()) return null;
        const c = coords(o);
        const fs = Math.round(clamp(o.fontSize, 12, 100) * dimensions.w / 360);
        const color = kind === 'logo' ? '0x75f0c5' : 'white';
        const box = kind === 'subtitle' || kind === 'text' ? ':box=1:boxcolor=black@0.55:boxborderw=8' : ':box=1:boxcolor=0x10182a@0.8:boxborderw=5';
        return "drawtext=fontfile=/usr/share/fonts/truetype/noto/NotoSansMyanmar-Regular.ttf:text='" + esc(o.text).slice(0, 250) + "':x=" + c.x + ':y=' + c.y + ':fontsize=' + fs + ':fontcolor=' + color + box;
      };
      let graph = '';
      let current = 'v0';
      if (blur) {
        graph = '[0:v]' + filters.join(',') + '[base];[base]split[main][blurSrc];[blurSrc]crop=' + blur.w + ':' + blur.h + ':' + blur.x + ':' + blur.y + ',boxblur=' + clamp(s.blurAmount, 4, 30) + ':2[blurred];[main][blurred]overlay=' + blur.x + ':' + blur.y + '[v0]';
      } else {
        graph = '[0:v]' + filters.join(',') + '[v0]';
      }
      for (const kind of ['subtitle', 'text', 'logo']) {
        const dt = drawtext(kind, overlays[kind]);
        if (dt) {
          const next = 'v' + (Number(current.slice(1)) + 1);
          graph += ';[' + current + ']' + dt + '[' + next + ']';
          current = next;
        }
      }
      job.progress = 15; job.message = 'Rendering video with FFmpeg';
      const args = ['-y', '-i', file.path];
      if (voicePath && fs.existsSync(voicePath)) args.push('-i', voicePath);
      args.push('-filter_complex', graph, '-map', '[' + current + ']');
      if (voicePath && fs.existsSync(voicePath)) {
        args.push('-map', '1:a:0', '-af', 'apad', '-shortest');
      } else {
        args.push('-map', '0:a:0?');
      }
      args.push('-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', '-progress', 'pipe:1', outputPath);
      const child = spawn('ffmpeg', args, { stdio: ['ignore','pipe','pipe'] });
      let stderr = '';
      child.stdout.on('data', chunk => {
        const txt = chunk.toString();
        const m = txt.match(/out_time_ms=(\d+)/);
        if (m && file.duration > 0) job.progress = Math.max(15, Math.min(95, 15 + Math.round(Number(m[1]) / 1000000 / file.duration * 80)));
      });
      child.stderr.on('data', d => { stderr += d.toString(); if (stderr.length > 12000) stderr = stderr.slice(-12000); });
      child.on('error', err => { job.state = 'failed'; job.message = safeError(err); });
      child.on('close', code => {
        if (code === 0 && fs.existsSync(outputPath)) {
          job.state = 'done'; job.progress = 100; job.message = 'Export complete'; job.downloadUrl = '/api/output/' + id;
        } else { job.state = 'failed'; job.message = stderr.slice(-1500) || 'FFmpeg export failed.'; }
      });
    } catch (e) { job.state = 'failed'; job.message = safeError(e); }
  })();
});

app.get('/api/source/:id', (req, res) => {
  const file = uploads.get(String(req.params.id || ''));
  if (!file || !fs.existsSync(file.path)) return res.status(404).json({ error: 'Source video not found.' });
  res.type(path.extname(file.path));
  res.sendFile(file.path);
});
app.get('/api/jobs/:id', (req, res) => {
  const job = jobs.get(req.params.id);
  if (!job) return res.status(404).json({ error: 'Job not found' });
  res.json(job);
});
app.get('/api/output/:id', (req, res) => {
  const id = String(req.params.id).replace(/[^a-f0-9-]/gi, '');
  const candidates = [path.join(OUTPUT_DIR, id + '.mp4'), path.join(OUTPUT_DIR, id + '.wav')];
  const file = candidates.find(p => fs.existsSync(p));
  if (!file) return res.status(404).json({ error: 'Output file not found. Files may be temporary on an ephemeral disk.' });
  res.download(file);
});

app.use((err, _req, res, _next) => res.status(500).json({ error: safeError(err) }));
app.listen(PORT, '0.0.0.0', () => console.log('Speed Recap listening on ' + PORT + '; data directory: ' + DATA_DIR));
