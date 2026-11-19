# clip-watcher

```
pip install streamlink openai-whisper   # plus ffmpeg from your package manager
./watch.sh <twitch_channel> [--min N] [--factor 2.5] [--bucket 10] [--model base]
```

Records the live stream to `rec/`, reads chat anonymously (`justinfan12345`, no token), counts messages in 10 s buckets,
and on a spike (>= 2.5x the median of the last 3 min and >= `--min` messages) cuts 20 s before + 10 s after into `clips/`
as a center-cropped 9:16 mp4, with a Whisper `.srt` next to it.

- Quiet chats: use `--min 5`. Default `--min` is 15.
- Only works while the channel is live. Ctrl-C stops and finishes pending clips.
- Clip streamers who allow it.
