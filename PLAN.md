# ProofBox plan

## Pipeline (gates/)

| Step | File | What it does |
|---|---|---|
| Track | `track.py` | ffmpeg to 10 fps, short edge <= 720, H.264. Upload once; SAM 3.1 video once per concept: `box`, `shipping label`, each order item. Saves `gates/out/<clip>_tracks.json`; reports ID stability and when items enter/leave the box. |
| Cuts | `cuts.py` | No AI. Hard cuts = one-frame spikes in frame difference; track jumps = object moves > 25% of the frame diagonal in one frame; box out of view > 3 s. |
| Compare | `compare.py` | One Muse Spark call per clip: listing screenshot + 3 sharpest crops per item (one per third of its visible time), label crops, closed-box frames (buyer) or last frames (seller). Strict JSON. |
| Rules | `rules.py` | Shopee rules in plain code -> headline verdict + every reason. |
| Batch | `run_all.py` | All `gates/clips/*.mp4` vs `gates/answer_key.csv` -> `gates/RESULTS.md`. |
| Cache | `cache.py` | Every API response on disk + spend ledger + budget stop. |

## How each verdict is decided (rules.py)

| Verdict | Signal |
|---|---|
| FAIL wrong item | No ordered item both tracked >= 1 s by SAM and judged `same_kind` by Spark |
| FAIL missing item | Some ordered items present, others not |
| FAIL mismatch | Present item with Spark `matches_listing=false` |
| FAIL quantity | Most copies SAM sees at once < ordered quantity |
| FAIL cut | Any hard cut (frame-difference spike) |
| FAIL rule length / size | Duration > 60 s / file > 30 MB |
| FAIL rule label | `shipping label` tracked < 0.5 s, or Spark `label_readable=false` |
| FAIL rule seal (seller) | Spark `seal_visible=false` on the last frames |
| FAIL rule sides (buyer) | Spark `box_sides_seen` < 4 on 8 closed-box frames before the first item appears |
| FLAG power test no display | Spark `power_test=no_display` |
| WARN continuity | Track jumps or box out of view > 3 s (no hard cut) |
| PASS tested working | No issues and Spark `screen_state=on` |

## Status (2026-11-02)

- Preflight: PASS. The key sees 9 models; `muse-spark-1.3`, `sam-3.1`, `muse-voice-transcribe-1.0`, `muse-image-1.0` all OK; Spark replied.
- Self-checks: `track.py --selftest`, `cuts.py --selftest`, `rules.py` pass.
- Dry run on one public Shopee unboxing (59 s, pouch with highlighter pens, edited with title cards; not committed):
  verdict FAIL cut. Cuts at 0.6-0.8, 6.1, 9.6, 16.3, 57.2 s. SAM: box 4 IDs over 29.8 s, highlighter 16 IDs (16 = per-request cap),
  shipping label 0 detections. Spark: highlighter same kind, matches listing (stand-in listing = a frame of the video).
  39 track jumps. Spend $0.51 (4 SAM calls at $0.118, 2 Spark calls).
- Fixed during the dry run: the sharpness pick chose burned-in captions, so items are now cropped around all copies;
  `label` matched product stickers, so the concept is now `shipping label`; label tracks are left out of the jump check.

## Weak spots to calibrate on the first real clips

- SAM nouns are guesses (`CONCEPT` in run_all.py: multitester -> `multimeter`, orange pi -> `circuit board`, ...). Check `gates/out/<clip>/` crops.
- `shipping label` is untested on a real waybill. If G1 shows 0 detections, try `sticker` or `paper`.
- Track jumps are noisy when many identical items swap IDs (39 on 16 pens) and may cause false WARN continuity. Knob: `JUMP_FRAC` in cuts.py.
- Hard-cut thresholds `CUT_MIN` / `CUT_RATIO` (cuts.py); `SIDES_MIN` = 4 (rules.py; the rule says all 6).
- Power test relies on 3 crops per item.
- Spark price is not in our notes; the ledger assumes $1 / $4 per 1M tokens (`SPARK_USD_IN/OUT`). SAM cost is exact ($0.20 / 1000 frames).

## Cost

SAM bills every frame: a 60 s clip at 10 fps = 600 frames = $0.12 per concept. With box + label + 1-2 items:
about $0.36-0.48 per clip plus ~$0.02 Spark. All 20 clips ~$8-10; with all `_b` takes ~$16-20. `--fps 5` or 30-40 s clips halve it.
The ledger budget defaults to $2 total and $0.51 is used, so raise it before a full run.

## Tomorrow: filming checklist

Use a cardboard box (SAM concept is `box`), steady phone, good light, one take per row, <= 60 s unless noted.
Name files by row: `gates/clips/G1.mp4`, second take `G1_b.mp4`.

- Everyone except U5: before opening, turn the closed box slowly so all 6 sides show (~1 s each).
- Everyone except U6: hold the shipping label to the camera ~2 s, readable.
- Sellers except P6: end on a close-up of the sealed box with tape.
- G3: stop and restart recording mid-video (or splice two takes) = a cut.
- P7: no cut, but point the camera away from the box for 4+ s mid-video.
- P8: record longer than 60 s.
- U8: multitester without battery; turn the dial/press power and show the blank screen. P9: turn it on, show a reading.
- Listing screenshots in `gates/listings/`, named by item: `router.png`, `charger.png`, `multitester.png`, `probes.png`,
  `shirt A.png`, `orange pi.png`, `usb drive.png` (missing ones fall back to the item name).

## Run

```
python gates/run_all.py                    # all clips; writes gates/RESULTS.md
$env:PROOFBOX_BUDGET = "12"                # PowerShell: allow more spend first
python gates/track.py gates/clips/G1.mp4 box "shipping label" router charger   # tracking only
```
Uploaded file IDs are cached; if SAM says a file is missing, delete `gates/cache/upload/`.
