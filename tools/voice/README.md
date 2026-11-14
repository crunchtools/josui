# Voice into Josui Prime

Push-to-talk on the host that runs Josui Prime (Dolza). The microphone never
enters a container: `ptt` records on the host, a local whisper.cpp server turns
the audio into text, and the text is typed into Prime's tmux session the way a
person at the keyboard would type it.

```
mic ─ pw-record ─ ptt ─ HTTP 127.0.0.1:8178 ─ whisper.cpp (Vulkan, 890M)
                   └─ podman exec josui tmux send-keys -t prime
```

## Pieces

- `whisper.container`: the quadlet. It runs the upstream
  `ghcr.io/ggml-org/whisper.cpp:main-vulkan-<sha>` image, read-only and with no
  capabilities. It gets `/dev/dri/renderD128`, publishes on loopback only, and
  reads the model from `~/srv/whisper/models`.
- Model: `ggml-large-v3-turbo-q5_0.bin` from `huggingface.co/ggerganov/whisper.cpp`.
- `ptt`: symlinked into `~/.local/bin`. The first press starts recording and
  the second sends the clip.
- Shortcut: a GNOME custom keybinding that runs `ptt`.
- `speak` and `josui-speak.service`: the reply voice. `speak` is symlinked
  as `~/.local/bin/josui-speak`. It uses Piper (`uv tool install piper-tts`)
  with the `en_US-ryan-high` voice from `huggingface.co/rhasspy/piper-voices`,
  in `~/srv/piper/voices`. It renders a sentence in about 1.6 s on the CPU.
  Code blocks, links and markdown are dropped. An answer longer than
  `SPEAK_MAX_CHARS` (900) is cut at a sentence, and the rest stays on screen.

## Measured on Dolza (2026-11-14)

The whisper.cpp sample clip `jfk.wav` (11 s) was transcribed with the turbo
q5_0 model.

| Backend | Time per clip |
|---------|---------------|
| CPU, 12 threads | 8.8–9.4 s |
| Vulkan on the Radeon 890M | 0.9–1.6 s |

The Vulkan backend is the reason the server runs the `-vulkan` image.

## Settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `PTT_SOURCE` | the default source | PipeWire node to record from (`pactl list short sources`) |
| `PTT_WHISPER_URL` | `http://127.0.0.1:8178` | The whisper server |
| `PTT_TARGET` | `josui:dev` | `container:tmux-target` that the text is sent to |
