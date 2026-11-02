# ProofBox: rules for Claude

ProofBox checks a phone packing (seller) or unboxing (buyer) video against the order and the
marketplace's evidence rules, then gives a verdict with reasons. SAM 3.1 tracks every item, the box
and the label; Muse Spark compares items with the listing screenshot and reads the label; plain code
applies the rules and detects cuts. See PLAN.md.

- Every model called at runtime is a Meta model (Meta Model API: `sam-3.1`, `muse-spark-1.3`).
  Classical image math (OpenCV frame differences, Laplacian sharpness) is fine; no other detectors or models.
- Never print or commit the API key. It lives in the gitignored `.env` (`MODEL_API_KEY`);
  `common/meta_client.py` reads it.
- Never commit videos, audio, frames or crops (`gates/clips/`, `gates/cache/`, `gates/out/`, `media/` are gitignored).
- Every API call goes through `gates/cache.py` `cached()`: responses are cached on disk (re-runs cost nothing)
  and a spend ledger refuses calls past `PROOFBOX_BUDGET` (USD, default 2.00).
- SAM: one short concept per request. Spark: strict JSON schema.
- No app UI yet. Keep changes small; results in gates/RESULTS.md are measured numbers only.
