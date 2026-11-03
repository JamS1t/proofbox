"""ProofBox web app (FastAPI + plain HTML/JS). Uploads are deleted after processing.

    python -m uvicorn app.server:app --port 8800     # from the repo root, then open http://localhost:8800
"""
import json
import re
import shutil
import sys
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pipeline  # noqa: E402

UPLOADS, RESULTS, SAMPLES = HERE / "uploads", HERE / "results", HERE / "samples"
MAX_MB, MAX_S = 30, 60
JOBS = {}
ID = re.compile(r"[A-Za-z0-9_-]{1,40}")

app = FastAPI(title="ProofBox")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


async def save(upload, path, limit_mb):
    size = 0
    with open(path, "wb") as f:
        while chunk := await upload.read(1 << 20):
            size += len(chunk)
            if size > limit_mb * 1e6:
                raise HTTPException(413, f"{upload.filename} is over {limit_mb} MB.")
            f.write(chunk)


@app.post("/api/check")
async def start(video: UploadFile = File(...), mode: str = Form(...), marketplace: str = Form("shopee"),
                order_text: str = Form(""), order_images: list[UploadFile] = File(default=[])):
    if mode not in ("seller", "buyer"):
        raise HTTPException(400, "Choose Seller packing or Buyer unboxing.")
    shots = [i for i in order_images if i.filename]
    if not shots and not order_text.strip():
        raise HTTPException(400, "Add an order screenshot or type the order.")
    jid = uuid.uuid4().hex[:12]
    d = UPLOADS / jid
    d.mkdir(parents=True)
    try:
        vpath = d / f"video{Path(video.filename or 'v.mp4').suffix.lower() or '.mp4'}"
        await save(video, vpath, MAX_MB)
        if pipeline.track.probe(vpath)["duration"] > MAX_S + 0.5:
            raise HTTPException(400, f"The video is over {MAX_S} s.")
        files = []
        for n, s in enumerate(shots):
            p = d / f"order_{n}{Path(s.filename).suffix.lower() or '.png'}"
            await save(s, p, 10)
            files.append(p)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    JOBS[jid] = {"stage": pipeline.STAGES[0], "done": False, "error": None}
    threading.Thread(target=run, args=(jid, vpath, mode, files, order_text), daemon=True).start()
    return {"id": jid}


def run(jid, video, mode, files, text):
    job = JOBS[jid]
    try:
        kit = pipeline.read_order(files, text)
        out = RESULTS / jid
        out.mkdir(parents=True, exist_ok=True)
        pipeline.check(video, mode, kit, out, progress=lambda s: job.update(stage=s))
        job.update(stage="Verdict", done=True)
    except BaseException as e:  # includes the budget stop (SystemExit)
        job.update(error=str(e)[:300] or type(e).__name__, done=True)
    finally:
        prep = pipeline.track.DIR / "prep"
        for p in prep.glob(f"{pipeline.file_sha(video)[:16]}_*") if video.exists() else []:
            p.unlink(missing_ok=True)  # the downscaled copy of the upload
        shutil.rmtree(UPLOADS / jid, ignore_errors=True)


def folder(kind, rid):
    if not ID.fullmatch(rid) or kind not in ("job", "sample"):
        raise HTTPException(404)
    d = (RESULTS if kind == "job" else SAMPLES) / rid
    if not (d / "result.json").exists():
        raise HTTPException(404)
    return d


@app.get("/api/job/{jid}")
def job(jid: str):
    if jid not in JOBS:
        raise HTTPException(404)
    return JOBS[jid]


@app.get("/api/samples")
def samples():
    out = []
    for d in sorted(SAMPLES.glob("*/result.json")):
        r = json.loads(d.read_text(encoding="utf-8"))
        out.append({"id": d.parent.name, "verdict": r["verdict"], "mode": r["mode"], "product": r["product"]})
    return out


@app.get("/api/{kind}/{rid}/view", response_class=HTMLResponse)
def view(kind: str, rid: str):
    d = folder(kind, rid)
    res = json.loads((d / "result.json").read_text(encoding="utf-8"))
    return pipeline.render(res, lambda f: f"/api/{kind}/{rid}/frame/{f}")


@app.get("/api/{kind}/{rid}/frame/{name}")
def frame(kind: str, rid: str, name: str):
    if not re.fullmatch(r"frame_\d+\.jpg", name):
        raise HTTPException(404)
    return FileResponse(folder(kind, rid) / name)


@app.get("/api/{kind}/{rid}/evidence")
def evidence(kind: str, rid: str):
    d = folder(kind, rid)
    res = json.loads((d / "result.json").read_text(encoding="utf-8"))
    return HTMLResponse(pipeline.evidence(res, d), headers={
        "Content-Disposition": f'attachment; filename="proofbox-evidence-{rid}.html"'})
