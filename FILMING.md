# Filming plan

Answer key: `gates/answer_key.csv` (same rows). If an item is missing, swap in a substitute and update both files.

## Orders

| Prefix | Order | Kit |
|---|---|---|
| router | TP-Link TL-WR840N | read from screenshots |
| orangepi | Orange Pi One 1GB | read from screenshots |
| multitester | ANENG SZ305 | read from screenshots |
| usb | SanDisk Ultra Luxe 32GB | read from screenshots |
| adapter | DC 12V 3A power adapter | read from screenshots |
| uvlight | Alonefire SV98 UV flashlight | flashlight + USB cable (listing packing list) |
| stand | Aluminium laptop stand, black | a pair = 2 |

Props (photos only, no order): Scrabble board, 10A relay module.

- **Screenshots:** `gates/orders/<prefix>_1.png`, `<prefix>_2.png`, ... (listing with "package includes" and order
  details with the Order ID). `python gates/orders.py` reads them into `gates/kits.json`. Check that file and fix it
  by hand if needed; hand edits are kept. Manuals, cards and installed batteries are marked `"checked": false`.
- **Props photos:** `gates/photos/`. **Clips:** `gates/clips/<clip>.mp4`, second takes `<clip>_b.mp4`.
  These folders and `gates/orders/` are gitignored.

## Every clip, unless its row says otherwise

- **Label:** one paper label per order with the last 6 digits of its Order ID, big dark digits (1 cm+), taped flat on the box.
- **Setup:** cardboard box, phone propped and steady, good light, one continuous take, 60 s or less.
- **Buyer:**
  1. Before opening, turn the closed box slowly so all 6 sides show, about 1 s each.
  2. Hold the label to the camera for 2 s.
  3. Open the box.
  4. Hold each item to the camera for 2 s, printed ratings facing the lens (adapters: the V/A sticker).
- **Seller:**
  1. Show the label for 2 s.
  2. Hold each item to the camera for 2 s, ratings facing the lens.
  3. Pack, close and tape the box.
  4. End on a 2 s close-up of the taped box.
- **Multitester (buyer):** switch it on and keep a reading on screen for 10 s or more. A buyer's multitester that never
  lights up is flagged.
- Break only the one thing the row asks for. A clip that breaks two rules reports the more serious one first.

## Clips

| Clip | Mode | Order | What to do | Expected |
|---|---|---|---|---|
| G1 (+G1_b) | buyer | router | full kit | PASS |
| G2 (+G2_b) | buyer | router | router label, only the Scrabble board inside | FAIL wrong item |
| G3 | buyer | router | full kit, filmed as two recordings: G3a = sides, label, opening; stop; G3b = taking items out. `run_all.py` joins them into G3.mp4 | FAIL cut |
| U1 | buyer | multitester | full kit, switch on, show a reading | PASS |
| U2 (+U2_b) | buyer | router | router kit with its adapter swapped for the 12V 3A adapter; show the 12V 3A sticker | FAIL mismatch (spec) |
| U3 (+U3_b) | buyer | multitester | kit without the probes; still switch it on | FAIL missing item |
| U4 | buyer | orangepi | orangepi label, only the 10A relay module inside | FAIL wrong item |
| U5 | buyer | usb | full kit, open straight away without turning the box (label still shown) | FAIL rule sides |
| U6 | buyer | orangepi | full kit, hand covers the label the whole time | FAIL rule label |
| U7 | buyer | router | kit without the adapter | FAIL missing item |
| U8 | buyer | multitester | full kit, battery removed; press power / turn the dial close to the camera, blank screen 3 s | FLAG power test no display |
| P1 | seller | orangepi | full kit, sealed | PASS |
| P2 (+P2_b) | seller | uvlight | flashlight only, USB cable left out | FAIL missing item |
| P3 | seller | adapter | pack the router's adapter instead of the 12V 3A; show its sticker | FAIL mismatch (spec) |
| P4 | seller | stand | only 1 stand of the pair | FAIL quantity |
| P5 (+P5_b) | seller | usb | full kit, sealed; hold the tiny drive close to the camera | PASS (tiny item) |
| P6 | seller | router | full kit, flaps closed but no tape | FAIL rule seal |
| P7 | seller | orangepi | full kit, no cut; carry the board out of the frame for 5 s, bring it back, pack it | WARN continuity |
| P8 | seller | router | full kit, take about 75 s | FAIL rule length |
| P9 | seller | multitester | switch on, show a reading 5 s, then pack and seal | PASS tested working |

## Run

1. Film G1 first and put it in `gates/clips/` alone.
2. Run `python gates/run_all.py`.
3. Check `gates/out/G1/` (crops Spark saw) and `gates/out/G1.json`. If an item or the label has no crops, change its
   `sam_noun` in `gates/kits.json`.
4. Add the rest of the clips and run again. Cached clips cost nothing to re-run.
