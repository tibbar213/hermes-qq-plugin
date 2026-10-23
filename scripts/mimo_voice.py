"""MiMo voice bridge for Hermes command providers; credentials arrive through env_passthrough."""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ENDPOINT = "https://api.xiaomimimo.com/v1/chat/completions"

def request(payload):
    key = os.environ.get("MIMO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("MIMO_API_KEY is missing")
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
        headers={"api-key": key, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("MiMo API returned HTTP " + str(exc.code)) from None
    if not result.get("choices"):
        raise RuntimeError("MiMo returned no choices")
    return result["choices"][0]["message"]

def tts(input_path, output_path, voice="mimo_default", model="mimo-v2.5-tts"):
    text = Path(input_path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("No speech text")
    fmt = Path(output_path).suffix.lower().lstrip(".")
    if fmt not in {"wav", "mp3"}:
        raise ValueError("MiMo output must be WAV or MP3")
    message = request({"model": model, "messages": [
        {"role": "user", "content": "自然口语，像熟悉的朋友聊天，语气轻松，不要播音腔"},
        {"role": "assistant", "content": text}],
        "audio": {"format": fmt, "voice": voice}, "stream": False})
    encoded = (message.get("audio") or {}).get("data")
    if not encoded:
        raise RuntimeError("MiMo returned no audio")
    audio = base64.b64decode(encoded, validate=True)
    if len(audio) < 44:
        raise RuntimeError("MiMo returned empty audio")
    Path(output_path).write_bytes(audio)

def asr(input_path, output_path, model="mimo-v2.5-asr", language="auto"):
    audio = Path(input_path)
    with tempfile.TemporaryDirectory(prefix="mimo-asr-") as tmp:
        if audio.suffix.lower() not in {".mp3", ".wav"}:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("ffmpeg is required to convert this audio format")
            wav = Path(tmp) / "input.wav"
            subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i",
                str(audio), "-ar", "16000", "-ac", "1", str(wav)],
                check=True, capture_output=True, timeout=60)
            audio = wav
        if audio.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("Audio exceeds bridge size limit")
        fmt = audio.suffix.lower().lstrip(".")
        message = request({"model": model, "messages": [{"role": "user",
            "content": [{"type": "input_audio", "input_audio": {
                "data": base64.b64encode(audio.read_bytes()).decode(), "format": fmt}}]}],
            "asr_options": {"language": language}, "stream": False})
        transcript = (message.get("content") or "").strip()
        if not transcript:
            raise RuntimeError("MiMo returned no transcript")
        Path(output_path).write_text(transcript, encoding="utf-8")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["asr", "tts"])
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model")
    parser.add_argument("--voice", default="mimo_default")
    parser.add_argument("--language", default="auto", choices=["auto", "zh", "en"])
    args = parser.parse_args()
    if args.mode == "tts":
        tts(args.input, args.output, args.voice, args.model or "mimo-v2.5-tts")
    else:
        asr(args.input, args.output, args.model or "mimo-v2.5-asr", args.language)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("MiMo voice failed: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
