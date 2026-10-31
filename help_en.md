# SeeName user guide

> In one line: it turns folders of unreadable names like `VID_20251011_143032.mp4` or `IMG_8932.jpg` into names you can actually read — **date taken + place + what is in the picture**.

## 1. What it does

- You pick a folder. The app reads each file's own **capture time** and **GPS position** (already inside the photo or video — nothing to fill in), takes a few frames, and asks a vision model **running on your own PC** to describe the scene in a few words.
- The three parts become the new file name, for example `视20251011成都肖家河_洗车.mp4` or `图20241219成都武侯区_猫侧脸特写.jpg`.
  - `视` marks a video, `图` marks a photo; then the date, then place and description joined by `_`. Any part that is missing is simply left out.
- Everything runs locally. **Your photos and videos never leave this computer.**
- Click `? User guide` at the top right any time to reopen this page.

## 2. Three minutes to a renamed folder

Pick a mode first, then go through three steps. On the very first launch a small three-step wizard pops up; tick `Don't show again` and it stays away.

1. **Pick a mode** (the row under the title bar):
   - `Smart rename` (default): the model looks at the picture and writes a description such as "cat close-up". Slower, but the names read like a person made them.
   - `Quick rename`: the model is never called; names come from capture time and GPS only, done in seconds.
   - On first launch the app picks one for your machine (a GPU gets `Smart rename`, CPU only gets `Quick rename`) and writes `Picked for you` next to it. Once you click one yourself it stops choosing for you. If you later change a single option in Setup, this row shows `Custom` instead.
2. **① 📁 Choose photo folder**: one big button — click it and pick a folder. Afterwards the button reads "folder name · 128 items"; an empty folder reads "empty folder", and the small line underneath says so too.
3. **② ▶ Start analysis**: click it. While it runs the button turns into a progress bar in place, with "Analyzing 3/128" and "avg 1.2s per item, about 2 minutes left" beside it. To stop, click `Stop` that appears next to it — it stops after the current file, and everything already done is kept.
4. **③ Review and rename…**: becomes clickable when the run finishes. Open it, fix what you want, then click `Apply renames`.
   - Just want a preview: stop here. **Nothing is renamed until you click Apply renames.**

If no model is downloaded yet, starting a run offers to open the model manager. Downloads come from ModelScope (China) or Hugging Face (international) and cover 4B / 2B models, with pause, resume and SHA-256 verification, and you can point it at an existing folder instead. The model library lives in `models` inside the program folder — copy that folder to another machine and it keeps working.

## 3. Parts of the window

Top to bottom:

- **Mode**: `Smart rename` / `Quick rename`, possibly with `Picked for you` or `Custom` next to it.
- **① 📁 Choose photo folder**: the big button.
- **A small line under it** saying what this run will do, e.g. "Videos: frames sent to the model · Photos: model writes a description · Speed depends on your machine: seconds per item with a GPU, much slower on CPU only". In the fully offline mode it says "fully offline, no model calls" instead.
- **② Buttons**: `▶ Start analysis`; while running it becomes a progress bar in place (no extra row). Next to it is `Review and rename…`, which only lights up once analysis finished.
- **Results** table: four columns — `Original name`, `New filename`, `Description`, `Status` — plus one text button `Open folder` at the top right of the header.
  - When the table is empty the middle says: "Pick a folder, click Start — I will take it from there".
  - Hover a row for half a second and a preview image plus "original → new" floats up.
  - **The Status column is clickable**: `Couldn't read it — click to see`, `Rename failed — click to see` and `Click to add time` jump straight to that row in the review window. The other states are `✓ Renamed`, `✓` and `—`.
- **Selected-row strip**: click a row and it shows the thumbnail, `original → new`, plus place · elapsed time · frames. When a name is clipped in the table, this is where you read it in full.
- **Bottom status bar**: one sentence on the left — `● Ready`, `● Analyzing 3/128`, `● Stopped · 3/128`. Click it to see the model state; if the model is not ready yet it jumps to the Model group in Setup. The version number sits on the right.
- **The run log is no longer on screen**: when you need it, the error dialog has `Copy log for the author`. When a run finishes a small toast summarises it (how many items, how many to rename); click `View full report` on it for the full report.
- **⚙ Settings** (top right): two parts — `Common` holds just three groups (scope, model, save mode); everything technical is folded under `▸ Advanced settings`, collapsed by default. Changes save instantly, no OK button.

## 4. Every setting, explained

**Common (the three groups you see when Setup opens)**

- **Scope**: the master switch `AI description`, plus `Videos`, `Photos` and `Write content description for photos`. After a change, the line under the big button on the main window immediately says what the next run will do.
- **Model**: the current-model drop-down plus `Model manager…`. The line below says it plainly: the model comes in two parts — keep them in one folder and the model manager wires it up.
- **Acceleration**: by default it only reports the verdict — "GPU detected — using Vulkan acceleration", or "No usable GPU acceleration — running on CPU (slower)". To pin it yourself, click `Change` on the right and pick `Auto` / `CUDA` / `Vulkan` / `CPU only`.
- **Save mode**: `Simple` (rename only, no extra files — **default**) or `Detailed` (also keeps the `.ai.txt` summary and `目录说明.md`, and writes file metadata). This is the master switch for the three detail options under Advanced.

**Advanced (only after clicking `▸ Advanced settings`)**

- **Analysis**
  - **Frames**: how many images per video go to the model. `Auto` picks by clip length (2 for short clips, 8 for long ones) — usually leave it. Raise it (4/6/8) if results look off; lower it for simple footage to go faster.
  - **Max items**: how many files this run may process; `0` means all. Put `10` here to try ten first.
  - **Reprocess already-renamed**: off by default. When on, files already named `视2024…` are analysed again; when off they are skipped, which suits batch-by-batch work.
- **Place names & location**
  - **Online precise location**: **off by default**. While off the app sends no network packets at all and place names come from the built-in offline database (district level). When on it uses an online map service for street/town level addresses; failures or no network fall back to the offline database without an error.
  - **Amap API key**: with online lookup on, this key resolves street-level addresses in mainland China. Get one free at lbs.amap.com; leave it blank to stay on the offline database (addresses outside China use Nominatim).
  - **Address detail**: how precise online lookup should be — `District` or `Street`.
- **How results are saved (details)**
  - **Write metadata into files**: puts the AI description inside the file itself (EXIF/XMP for photos, container tags for videos without re-encoding), so it travels with the file. Notes you wrote yourself are never overwritten.
  - **Write .ai.txt summaries**: a matching `.ai.txt` next to each file — first line the place, second line the description.
  - **Write folder summary (目录说明.md)**: after a batch, one `目录说明.md` at the folder root listing the files and what is in them.
- **GPU acceleration**: optional Vulkan / CUDA components. Install one to use the GPU; without one the CPU is used. GPU drivers must be installed separately. Each card has one button (`Download and enable` / `Enable` / `Enabled` / `Pause` / `Resume`), with `Use CPU (no acceleration)` below.
- **Language**: `中文` / `English`. Restart the app to apply.

## 5. After a run: review, rename, undo

Click `Review and rename…` (step 3) to open the big table. Columns are `#`, `Original name`, `Time taken`, `Location`, `Description`, `New filename after edits`:

- Time, location and description are **editable in place**; the new filename below updates as you type.
- An empty time means this row is not renamed.
- The `↓` on a cell fills that value down through consecutive blanks (one fill per scene is enough).
- `Find & replace…` swaps one wrong name that is scattered across many rows in one go.
- `Blanks only` shows just the rows still missing something.
- Page with `← Previous` / `Next →`; pick the page size under `Per page`.
- Nothing on disk changes until you click `Apply renames`. `Don't rename, close` keeps everything as is — the draft is saved and reopening shows the same table.

**Renamed by mistake?** Click `Undo last rename` at the bottom left of the review window; it restores this batch from the ledger (the ledger itself is untouched — only the filenames are swapped back). Note that the undo relies on that ledger: if you renamed these files by hand afterwards it cannot restore them, so keep a backup before large batches.

## 6. Common questions

- **Why does a file have no place name?**
  The file carries no GPS (phone location was off when it was taken). The app borrows from a nearby photo with GPS by capture time; if there is none the field stays empty. That is missing data, not a bug.

- **The place is too coarse — district only?**
  The offline database stops at district/county level. For street level, turn on `Online precise location` in Setup and fill in the `Amap API key`.

- **A status says "Click to add time"?**
  No capture time could be read from that file. Click that cell to jump to the row in the review window and type the time yourself (e.g. `2025-10-11 14:30`); the row then takes part in renaming.

- **A status says "Couldn't read it — click to see"?**
  The model returned nothing usable (blurry picture, unreadable video, …). Clicking jumps to that row in the review window; you can also press `Retry` to run again, and if it still fails use `Copy log for the author`.

- **Why is it slow?**
  The current build runs on the CPU by default, and the time depends on your GPU, the pictures and the description length. With a GPU, installing the acceleration component in Setup helps a lot; you can also switch to `Quick rename` (time and place only) or untick `Write content description for photos` to describe videos only. Stopping a run keeps the partial plan after the current file; quitting aborts inference and releases the model safely.

- **It says the model files are incomplete?**
  Re-download from `Model manager…`, or point it at a folder that has both the GGUF main model and the matching projector file. An Ollama blobs directory cannot be used directly. If several models share a name, pick the exact model folder — the check list says what is missing or duplicated.

- **Can it damage my originals?**
  Only filenames change — no re-encoding, no compression, the picture stays byte-for-byte the same. `Write metadata into files` only **adds** description fields; your own notes, capture time, GPS and camera model are never deleted or altered.

- **I changed the language but nothing happened.**
  The language setting needs an app restart.

- **Running the same folder again — will it duplicate anything?**
  Already-renamed files are skipped by default (tick `Reprocess already-renamed` to include them). Metadata and `.ai.txt` overwrite the same fields, so nothing piles up.

## 7. Privacy

- Analysis and renaming happen entirely on your computer. Photos and videos never leave it, and results are never uploaded.
- `Online precise location` is **off** by default: while off the app makes no network requests and all addresses come from the built-in offline database.
- With it on, only the **coordinates** are sent to a map provider to get a more precise address — never the file itself.
- Downloading a model is the only one-off network action (you can also drop GGUF files into `models` by hand). Once the model is there, turn online lookup off and the whole flow works offline.

## 8. About

Video probing and frame extraction run in-process through PyAV and need no separate FFmpeg executable. Writing video file metadata still requires the FFmpeg CLI; that part has not migrated yet (if it is missing from both the program folder and PATH, only that one option is unavailable — analysis and renaming are unaffected).

- SeeName (显影)
- See it. Name it. / 看见，即命名。
- Author: tanhua
- Version %VER%
- Place data © OpenStreetMap contributors (ODbL) · worldwide cities from GeoNames (CC-BY 4.0)
- Online lookups: Amap Open Platform (lbs.amap.com) in China · Nominatim / OpenStreetMap elsewhere
