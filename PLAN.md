# ProofBox plan

Filming and clip list: FILMING.md. Answer key: gates/answer_key.csv.

## Pipeline (gates/)

| Step | File | What it does |
|---|---|---|
| Orders | `orders.py` | Muse Spark reads each order's screenshots (`gates/orders/<prefix>*`) into `gates/kits.json`: product, last 6 digits of the Order ID, kit items with qty, SAM noun, printed spec, checked yes/no. Hand edits win. |
| Track | `track.py` | ffmpeg to 10 fps, short edge <= 720, H.264. Upload once; SAM 3.1 video once per concept: `box`, `shipping label`, each checked kit noun. Saves `gates/out/<clip>_tracks.json`; reports ID stability and when items enter/leave the box. |
| Cuts | `cuts.py` | No AI. Hard cuts = one-frame spikes in frame difference. Jump cuts = objects of 2+ concepts jump together. Continuity (seller) = box out of view > 3 s, or an item leaves the frame edge > 3 s and returns. |
| Compare | `compare.py` | One Muse Spark call per clip: order screenshots + 3 sharpest crops per kit item (one per third of its visible time, cut from the full-resolution original), label crops, closed-box frames (buyer) or last frames (seller). Strict JSON. |
| Rules | `rules.py` | Shopee rules in plain code -> headline verdict + every reason. |
| Batch | `run_all.py` | Joins `<clip>a` + `<clip>b` recordings, runs all clips vs the answer key -> `gates/RESULTS.md`. |
| Cache | `cache.py` | Every API response on disk + spend ledger + budget stop. |

## How each verdict is decided (rules.py)

| Verdict | Signal |
|---|---|
| FAIL wrong item | No kit item both tracked >= 1 s by SAM and judged `same_kind` by Spark |
| FAIL missing item | Some kit items present, others not |
| FAIL mismatch | Present item with Spark `matches_listing=false` (model, colour, printed rating vs kit spec + screenshots) |
| FAIL quantity | Most copies SAM sees at once < kit qty |
| FAIL cut | Hard cut or jump cut |
| FAIL rule length / size | Duration > 60 s / file > 30 MB |
| FAIL rule label | Spark's `label_text` doesn't contain the order's 6 digits (readability only if no Order ID) |
| FAIL rule seal (seller) | Spark `seal_visible=false` on the last frames |
| FAIL rule sides (buyer) | Spark `box_sides_seen` < 4 on 8 closed-box frames before the first item appears |
| FLAG power test no display | Buyer: a kit item with a display stays blank in every crop; or Spark sees a failed power-on |
| WARN continuity | Seller only: box out of view or item out of frame > 3 s (no cut) |
| PASS tested working | Seller, no issues, and a display was lit |

Expected-value notes in brackets ("(spec)", "(tiny item)") are ignored when scoring.

## Status

- 2026-11-02: preflight PASS (key sees 9 models; Spark, SAM, Transcribe, Image OK). Dry run on one public Shopee
  unboxing (59 s, edited, pouch with highlighters; not committed): FAIL cut, Spark matched the item. $0.51.
- 2026-11-03: switched to the real orders and clip plan. Re-ran the dry clip on the new pipeline (SAM cached, Spark only):
  still FAIL cut. Kit reading from a screenshot works ("2x highlighter pen", noun `highlighter`). G3a+G3b join tested
  locally. Ledger total $0.54.
- Waiting on: order screenshots in `gates/orders/` (not there yet), then clips.

## Weak spots to calibrate on the first real clips

- SAM nouns come from Spark's kit reading. Check `gates/out/<clip>/` crops and fix `sam_noun` in kits.json.
- `shipping label` found nothing in the dry clip. If G1 has no label crops, try `paper` or `label`.
- Thresholds: `CUT_MIN`, `CUT_RATIO`, `JUMP_FRAC`, `GAP_S`, `EDGE` (cuts.py); `SIDES_MIN` = 4 (rules.py; the rule says all 6).
- The power test relies on 3 crops per item, so keep the display on for a while.
- Spark price is not in our notes; the ledger assumes $1 / $4 per 1M tokens (`SPARK_USD_IN/OUT`). SAM cost is exact ($0.20 / 1000 frames).

## Cost

SAM bills every frame: 60 s at 10 fps = 600 frames = $0.12 per concept. Box + label + 2-3 kit items = $0.48-0.60
per clip, plus ~$0.02 Spark. All 26 recordings (20 clips + 6 second takes) ~$13-16. `--fps 5` halves it.
The ledger stops at `PROOFBOX_BUDGET` (default $2 total, $0.54 used), so raise it before a full run:
`$env:PROOFBOX_BUDGET = "18"`.

## Run

```
python gates/orders.py                     # read order screenshots -> gates/kits.json (check it)
python gates/run_all.py                    # all clips -> gates/RESULTS.md
python gates/track.py gates/clips/G1.mp4 box "shipping label" router   # tracking only
```
Uploaded file IDs are cached; if SAM says a file is missing, delete `gates/cache/upload/`.
