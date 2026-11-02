"""
Small helper for Meta's Model API (https://api.meta.ai/v1), used by all three tests.

Set your key once (PowerShell):   $env:MODEL_API_KEY = "your-key"
                     (bash/zsh):   export MODEL_API_KEY=your-key
META_API_KEY also works.

Formats follow Meta's official legal-agent-workshop client and public hackathon
repos that call these endpoints successfully (60-second-fire-check, spatialsight,
safepath-ai, nimbytes, snapclaim). Model IDs can be overridden with env vars.
"""
import base64
import json
import os
import re
import sys

BASE_URL = os.environ.get("MODEL_API_BASE_URL", "https://api.meta.ai/v1")
SPARK_MODEL = os.environ.get("MUSE_SPARK_MODEL", "muse-spark-1.3")  # or muse-spark-1.3-contributor (cheaper)
SAM_MODEL = os.environ.get("SAM_MODEL", "sam-3.1")
ASR_MODEL = os.environ.get("ASR_MODEL", "muse-voice-transcribe-1.0")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "muse-image-1.0")


def api_key():
    key = os.environ.get("MODEL_API_KEY") or os.environ.get("META_API_KEY")
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not key and os.path.exists(env):  # fall back to the repo's gitignored .env
        for line in open(env, encoding="utf-8"):
            if line.startswith("MODEL_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("Set MODEL_API_KEY (your dev.meta.ai key) first.")
    return key


def client():
    from openai import OpenAI
    return OpenAI(api_key=api_key(), base_url=BASE_URL, max_retries=2, timeout=180)


def data_url(image_bytes, mime="image/jpeg"):
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode()


# --------------------------------------------------------------------------- #
# Muse Spark: images in, strict JSON out (Responses API)
# --------------------------------------------------------------------------- #
def spark_json(instructions, images, schema, name="result", model=None):
    """images: list of (bytes, mime). schema: strict JSON schema (all keys required,
    additionalProperties false). Returns the parsed dict."""
    content = [{"type": "input_text", "text": instructions}]
    for b, mime in images:
        content.append({"type": "input_image", "image_url": data_url(b, mime)})
    resp = client().responses.create(
        model=model or SPARK_MODEL,
        input=[{"role": "user", "content": content}],
        text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
    )
    return json.loads(resp.output_text)


# --------------------------------------------------------------------------- #
# SAM 3.1 on one image with a short text prompt (Responses API only)
# --------------------------------------------------------------------------- #
BOX = re.compile(r"(\d+)<\|box;x1=(-?\d+);y1=(-?\d+);x2=(-?\d+);y2=(-?\d+);w=(\d+);h=(\d+)(?:;c=([0-9.]+))?\|>")


def sam_image_boxes(image_bytes, prompt, mime="image/jpeg"):
    """Returns [{'id','x1','y1','x2','y2','conf'}] in source-pixel coordinates.
    An empty list means SAM found no match (not an error). Short generic nouns work
    best ("red light", "disk"); long phrases often return nothing."""
    resp = client().responses.create(
        model=SAM_MODEL,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": data_url(image_bytes, mime)},
        ]}],
        metadata={"include_confidence": "true"},
    )
    text = resp.output_text or ""
    boxes = []
    for m in BOX.finditer(text):
        oid, x1, y1, x2, y2, w, h, c = m.groups()
        boxes.append({"id": oid, "x1": int(x1), "y1": int(y1), "x2": int(x2) + 1, "y2": int(y2) + 1,
                      "conf": float(c) if c else None})
    return boxes


# --------------------------------------------------------------------------- #
# Muse Voice Transcribe: batch file with speaker diarization
# --------------------------------------------------------------------------- #
def transcribe_wav(wav_path, mode="DIARIZATION", language_bias=None, keywords=None):
    """wav must be RIFF/WAVE mono 16-bit PCM at 16 or 24 kHz, <= 10 min, <= 32 MB.
    Returns the raw JSON: {'transcript', 'turns': [{'speaker','startMs','endMs','transcript',...}], ...}"""
    import requests
    req = {"mode": mode, "model": ASR_MODEL, "audioEncoding": "WAV"}
    if language_bias:
        req["languageBias"] = language_bias  # language NAMES, e.g. ["Tagalog", "English"]
    if keywords:
        req["keywords"] = keywords[:20]
    with open(wav_path, "rb") as f:
        r = requests.post(
            f"{BASE_URL}/asr/transcribe",
            headers={"Authorization": f"Bearer {api_key()}", "Accept": "application/json"},
            files={"request": (None, json.dumps(req), "application/json"),
                   "audio": ("audio.wav", f, "audio/wav")},
            timeout=600,
        )
    if r.status_code != 200:
        sys.exit(f"Voice Transcribe error {r.status_code}: {r.text[:500]}")
    return r.json()


# --------------------------------------------------------------------------- #
# Muse Image edit (no mask support: describe the change in the prompt)
# --------------------------------------------------------------------------- #
def image_edit(image_bytes, prompt, mime="image/png", output_format="png"):
    import requests
    body = {
        "model": IMAGE_MODEL, "prompt": prompt,
        "images": [{"image_url": data_url(image_bytes, mime)}],
        "n": 1, "response_format": "b64_json", "output_format": output_format,
    }
    r = requests.post(f"{BASE_URL}/images/edits", json=body,
                      headers={"Authorization": f"Bearer {api_key()}"}, timeout=300)
    if r.status_code != 200:
        sys.exit(f"Muse Image edit error {r.status_code}: {r.text[:500]}")
    return base64.b64decode(r.json()["data"][0]["b64_json"])
