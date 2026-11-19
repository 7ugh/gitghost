"""Hardware + tool detection, and local-model recommendations."""
from __future__ import annotations
import json, os, platform, re, shutil, subprocess
from . import util


def _sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def ram_gb() -> float:
    try:
        if platform.system() == "Darwin":
            return int(_sh(["sysctl", "-n", "hw.memsize"])) / 2**30
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal"):
                return int(line.split()[1]) / 2**20
    except Exception:
        pass
    return 0.0


def recommend_models(ram: float, apple_silicon: bool) -> dict:
    """Conservative picks that leave RAM for FFmpeg/Pillow and macOS itself."""
    if ram >= 32:
        llm = "qwen2.5:14b-instruct"
    elif ram >= 16:
        llm = "qwen2.5:7b-instruct"
    elif ram >= 8:
        llm = "llama3.2:3b-instruct"
    else:
        llm = "llama3.2:1b-instruct"
    return {
        "llm_ollama": llm,
        "tts": "macOS `say` (built in, free)" if platform.system() == "Darwin" else "piper (en_US-lessac-medium) or espeak-ng",
        "image_gen": ("ComfyUI + SDXL-Turbo/FLUX-schnell (Metal) is feasible but slow; start with the built-in procedural visuals"
                      if apple_silicon and ram >= 16 else
                      "Skip local image generation at this RAM; use procedural visuals or your own authorized footage"),
        "note": "Local video-diffusion models (e.g. AnimateDiff/Wan) need far more memory than most Macs have; this app animates with Python/FFmpeg instead.",
    }


def report() -> dict:
    sysname = platform.system()
    mac = sysname == "Darwin"
    chip = _sh(["sysctl", "-n", "machdep.cpu.brand_string"]) if mac else ""
    if not chip:
        m = re.search(r"model name\s*:\s*(.+)", open("/proc/cpuinfo").read()) if os.path.exists("/proc/cpuinfo") else None
        chip = m.group(1) if m else platform.processor()
    apple = mac and platform.machine() == "arm64"
    ram = ram_gb()
    tools = {}
    for t in ("ffmpeg", "ffprobe", "python3", "ollama", "say", "piper", "espeak-ng", "node"):
        tools[t] = util.have(t)
    flite = False
    if tools["ffmpeg"]:
        out = _sh(["ffmpeg", "-hide_banner", "-filters"])
        flite = " flite " in out
        enc = _sh(["ffmpeg", "-hide_banner", "-encoders"])
        tools["libx264"] = "libx264" in enc
        tools["h264_videotoolbox"] = "h264_videotoolbox" in enc
    tools["ffmpeg_flite_tts"] = flite
    try:
        import PIL, numpy
        tools["pillow"], tools["numpy"] = PIL.__version__, numpy.__version__
    except Exception:
        tools["pillow"] = tools["numpy"] = None
    disk = shutil.disk_usage(os.getcwd())
    r = {"os": f"{sysname} {platform.release()}", "arch": platform.machine(), "chip": chip, "apple_silicon": apple,
         "cpu_cores": os.cpu_count(), "ram_gb": round(ram, 1), "disk_free_gb": round(disk.free / 2**30, 1),
         "tools": tools, "recommended": recommend_models(ram, apple)}
    r["ollama_models"] = _sh(["ollama", "list"]).splitlines()[1:] if tools["ollama"] else []
    return r


if __name__ == "__main__":
    print(json.dumps(report(), indent=2))
