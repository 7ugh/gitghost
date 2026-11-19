# ViralForge: your own approval-first AI video studio

Research trends → rank ideas → write, voice, caption and render a finished 1080×1920 Reel → **you approve** → publish (or post by hand) → learn from real analytics.

Runs on your Mac with free tools: Python, FFmpeg, Pillow/numpy, optional [Ollama](https://ollama.com) for local LLM scripts, macOS `say` or Piper for voices. No paid APIs, no subscriptions. Nothing is ever published without your explicit approval.

## Install (macOS)

```bash
brew install ffmpeg python@3.12
brew install ollama && ollama serve &            # optional but strongly recommended (story / comedy / what-if scripts + better paraphrasing)
cd viralforge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m viralforge hardware                    # checks your Mac + recommends a local model
ollama pull qwen2.5:7b-instruct                  # (use the model `hardware` recommends for your RAM)
python -m viralforge selftest                    # runs the whole test-suite on YOUR machine first (needs ffmpeg, ~5 min)
python -m viralforge serve                       # open http://localhost:8765
```

Phone access (same Wi-Fi): `python -m viralforge serve --host 0.0.0.0`, then open `http://<your-mac-ip>:8765/?token=<printed token>`.

## First video in one command (no internet needed)

```bash
python -m viralforge make --topic "TIL octopuses have three hearts" --notes viralforge/fixtures/octopus_notes.json --category facts
```
Output lands in `data/videos/<id>/` (`final.mp4`, `thumb.jpg`, `captions.srt`, `project.json`) and in the dashboard's **Approve** tab.

## What each part really does

| Area | Implementation | Honest limits |
|---|---|---|
| Research | Reddit public JSON, Hacker News, Google Trends RSS, Wikipedia most-read, news RSS, YouTube (official Data API, free key, optional) | **Instagram Reels and TikTok are not scraped** (no sanctioned public API; against their terms). Add what you see there via *Research → Add an observation*. Live fetchers could not be run in the dev sandbox (network blocked); they are unit-tested on canned payloads and fail visibly, never silently. Reddit may require OAuth at scale. |
| Idea ranking | 8 transparent components with public weights, per-idea explanation, sensitive-topic filter (shows what it excluded) | Heuristics, **not predictions**. "Competition" is a proxy from the sample only. |
| Script | Local LLM via Ollama (JSON-mode prompt that forbids facts outside the notes) or a deterministic template for factual explainers | Template mode mostly re-uses your notes' wording (QC flags this). Story/comedy/what-if **require** an LLM; the app refuses rather than output junk. |
| Fact check | Every claim must trace to a note sentence (content-word + number match), reports single-source facts | Traceability, not truth: if the notes are wrong, the claim passes. Verify before posting. |
| Voice | piper > macOS `say` > espeak-ng > ffmpeg-flite (robotic, test fallback only) | Only flite was exercised in the dev sandbox; **run the test-suite on your Mac** to exercise `say`. Word highlight timing is an estimate (proportional), not forced alignment. |
| Visuals | Procedural animated motifs (hearts, orbs, bars, stat counters…), Ken-Burns on your own images, your own authorised video clips (`scene.image` / `scene.clip` + licence note) | **No AI image/video generation yet.** Local video diffusion is not realistic on most Macs. A ComfyUI image provider is the natural next step (the renderer already accepts `scene.image`). |
| Music/SFX | Generated from scratch with numpy (royalty-free by construction), ducked under narration | Simple loops, not a composer. |
| Captions | Word-highlight burned in, safe-zone placement, editable in the dashboard (SRT export too) | |
| QC | resolution/fps/codec, loudness (EBU R128), black/frozen frames, caption readability & sync, hook, fact trace, copy-overlap, asset licences, caption limits | Cannot judge whether it's *good*. |
| Publish | Official Instagram Graph API (resumable upload or `video_url`), hard approval gate in code, manual ZIP fallback | **Not verified against Meta's live servers** (tested against a local mock). Do a first test post. Needs an Instagram *professional* account + token (see below). |
| Analytics | Graph API insights sync + CSV import; weighted engagement per 100 reach; shrinkage-based category/style learning with confidence | Per-video "followers gained" is not exposed by the API; account follower snapshots are stored instead. Nothing is learned until you have ≥3 posted videos. |
| Daily automation | `tick` (launchd/cron) runs one research→video job per day, publishes only approved+due posts | Mac must be awake. Never auto-regenerates a rejection. Pauses when ≥3 videos await approval. |

## Instagram publishing setup (free)

1. Switch your Instagram to a **Professional** (Creator/Business) account.
2. Create a Meta developer app with the *Instagram API* product, add yourself as tester, generate a long-lived token with `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_insights`. For your own account no app review is needed.
3. Put the secrets in `data/.env` (chmod 600), never in settings:
   ```
   IG_ACCESS_TOKEN=...
   YOUTUBE_API_KEY=...      # optional, free quota
   ```
4. Dashboard → Settings → set your Instagram user id. Until then **POST NOW** produces a manual ZIP (video, cover, caption) to post from the app.

AI disclosure: captions include "Made with AI-generated visuals and voice" (toggle in settings); turn on Instagram's own AI label in the app when prompted.

## Daily automation

```bash
python -m viralforge install-schedule     # prints a launchd plist + cron line
```
Then enable it in Settings → Daily automation. Each day it creates **one** video, runs QC and waits in *Approve*.

## Commands

`hardware · voices · research [--fixture] · ideas · make · daily [--dry] · serve · tick · sync · install-schedule`

## Costs

$0 by default. Optional: a YouTube Data API key (free quota). Instagram's API is free. Nothing here enables a paid service.

## Layout

`viralforge/` research.py · ideas.py · notes.py · scriptgen.py · factcheck.py · tts.py · audio.py · render.py · thumbnail.py · describe.py · qc.py · pipeline.py · publish.py · analytics.py · scheduler.py · server.py · web/ · `tests/`
