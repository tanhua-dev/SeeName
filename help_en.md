# SeeName user guide

Video probing and frame extraction use PyAV in-process and need no FFmpeg executable. Writing video file metadata still requires the FFmpeg CLI; that operation has not migrated yet.

> In one line: it turns folders of unreadable names like `VID_20251011_143032.mp4` or `IMG_8932.jpg` into names you can actually read — **date taken + place + what is in the picture**.

## 1. What it does

- You pick a folder. The app reads each file's own **capture time** and **GPS position** (already inside the photo or video — nothing to fill in), takes a few frames, and asks a vision model **running on your own PC** to describe the scene in a few words.
- The three parts become the new file name, for example `视20251011成都肖家河_洗车.mp4` or `图20241219成都武侯区_猫侧脸特写.jpg`.
  - `视` marks a video, `图` marks a photo; then the date, then place and description joined by `_`. Any part that is missing is simply left out.
- Everything runs locally. **Your photos and videos never leave this computer.**
- Click `? User guide` at the top right any time to reopen this window.

## 2. Three steps to start

1. **Pick a folder**: type a path in the `Folder` box, or click `Browse…`. The drop-down on the right holds saved preset folders — click one to jump to it.
2. **Pick what to process**: under ` Scope `, tick `Videos` and/or `Photos`; the `AI description` box at the front is the master switch.
   - `AI description` is on by default: with it off, neither videos nor photos go through the model — files are named by `capture time + place` only, and the run finishes in seconds.
   - `Write content description for photos` is on by default: each photo spends additional CPU time with the model and comes back with a description like "cat close-up". Untick it for a fast pass that only reads time and GPS. It is a sub-option of `AI description` and is greyed out when the master switch is off.
3. **Click `▶ Start recognition`**: when the progress bar finishes, results appear in the `Results` table below.
   - To actually rename files: bottom-right `⋯ More` → `Review and rename…` → check, then click `Apply rename`.
   - To just preview: stop here. **Nothing is renamed until you click `Apply rename`.**

If no local models are found, Model manager offers downloads of 4B / 2B Q4 / Q8 pairs from Hugging Face or ModelScope, with cancellation, resume and SHA-256 verification. Existing directories remain supported. Default roots: Windows `%LOCALAPPDATA%\SeeName\models`, macOS `~/Library/Application Support/SeeName/models`, Linux `${XDG_DATA_HOME:-~/.local/share}/SeeName/models`; pairs live in `Qwen3-VL-4B-Instruct-GGUF` / `Qwen3-VL-2B-Instruct-GGUF` subdirectories. A custom model directory also becomes the download target. Uses in-process llama.cpp on CPU, without Ollama.

Models load when recognition starts, or when you click the status indicator. Changing the model or directory releases the old model without moving weights. The vision encoder initializes during the first image request.

## 3. Parts of the window

- **Folder / Browse… / preset drop-down**: choose the folder to process.
- ** Scope **: the master switch `AI description` first, then `Videos`, `Photos`, and `Write content description for photos` (a sub-option of the master switch).
- **`▶ Start recognition`**: starts the run. While it runs, `Stop recognition` appears next to it; clicking it stops after the current file, and everything already done is kept.
- **Progress bar + status light**: the light on the right reads `● Not started`, `● Recognizing…`, `● Stopping…`, `● Stopped manually · x/y done` or `● Done · x items`.
- **Results table**: columns are `#`, `Original name`, `New name`, `Place`, `Description`, `Time`, `Status`. Below it are `Copy all` and `Clear`.
- **Right sidebar**: only `What the model sees` (the image actually sent to the model). Click `»` at the top right to collapse it; collapsed, only a `⚙` remains — click that to open settings.
- **`⚙ Settings`** (top right of the window): every option lives on this one page — recognition, models, place lookup, output, GPU acceleration, language. Changes save immediately. Checkboxes are a box with a tick, and the whole row is clickable.
- **Bottom status bar**: model readiness/loading/error state, then `llama.cpp · CPU`, then the model directory. Click the status to load in advance; hover for errors or full paths.
- **`⋯ More`** (bottom right): `Review and rename…`, `Roll back renames in this folder`, `Open this folder`, `Open recognition report`, `Open rename ledger`, `Model manager…`, `About`, and the run-log toggle.
- **`▸ Run log`**: expands to show details for each file. It opens by itself when something goes wrong.

## 4. Every setting, explained

- **AI description**: **master switch, on by default**. While on, videos are sampled into frames and photos (if `Write content description for photos` is ticked) are sent to the local vision model, which is what produces descriptions like "cat close-up". **With it off the model is never called**: no frame extraction, no model pass — everything is named by `capture time + place` only and finishes in seconds. Use it on slow machines, or when you just want files sorted by time and place first. While off, `Write content description for photos` is greyed out and the app no longer asks you to prepare a local model first.
- **Frames per video**: how many images are sent to the model. `Auto` picks by length (2 for short clips, 6 for long ones) — usually leave it. Raise it (4/6/8) if results look off; lower it for simple footage to go faster.
- **Max items**: how many files this run handles; `0` means all. Set `10` to try a small batch first.
- **Re-process already renamed**: off by default. Tick it to re-recognize files that already have a `视2024…` style name; leave it off to skip them and work in batches.
- **Model**: select 4B / 2B Q4 / Q8 or import existing 4B F16 weights. Default: 4B Q4. Model manager supports downloads and local directory selection.
- **Online precise location**: **off by default**. While off the app sends no network requests at all and places come from the built-in offline database (district level). Turn it on to query an online map service for street/town level names — if it fails or you are offline, it silently falls back to the offline database.
- **Amap API key**: with online location on, this is used for coordinates inside China to get street-level addresses. Get a free key at lbs.amap.com; without it, Chinese coordinates keep using the offline database (coordinates outside China are unaffected — they use Nominatim).
- **Address detail**: how precise online lookups should be — `District` or `Street`.
- **Write file metadata**: writes the generated long description into the file itself (EXIF/XMP for photos, container tags only for videos — never re-encoded), so it travels with the file. Anything you wrote by hand is never overwritten.
- **Create .ai.txt summary**: writes a `.ai.txt` next to each file — first line place, second line the description.
- **Create 目录说明.md**: after a batch, writes a `目录说明.md` in the folder root summarizing everything.
- **Language**: `中文` / `English`. **Restart the app** for it to take effect.

## 5. After recognition: review, rename, roll back

Open `⋯ More` → `Review and rename…`. The big table has columns `Original name`, `Capture time`, `Place`, `Description`, `Resulting new name`:

- Time, place and description are **editable**; the new name below updates as you type.
- Leaving time empty means this file is not renamed.
- The `↓` next to a cell fills that value down into the following empty cells (one entry covers a whole scene).
- `Find and replace…`: fix the same wrong name scattered across many rows in one go.
- `Only empty`: show only the rows still missing a value.
- Move between pages with `← Previous` / `Next →`; page size is set next to `Per page`.
- Click `Apply rename` to actually rename; `Close without renaming` leaves files alone.
- After renaming, a result window appears where you can `Copy list` or open the rename ledger.

**Renamed something by mistake?** `⋯ More` → `Roll back renames in this folder` restores the batch using the ledger. This relies on the ledger, so if you later renamed those files yourself by hand it can no longer find them — back up before a large batch.

## 6. FAQ

- **Why do some files have no place name?**
  The file has no GPS (location was off when it was shot). The app borrows from nearby photos that do have GPS, and leaves it blank if there are none nearby — that is missing data, not a bug.

- **Place names are too coarse, only district level?**
  That is what the offline database stores. For street level, turn on `Online precise location` and fill in an `Amap API key`.

- **It says "(time blank, not renamed)"?**
  No capture time could be read from that file. Open `Review and rename…` and type one into the `Capture time` cell (for example `2025-10-11 14:30`) — it will then be renamed.

- **It is slow.**
  This version uses CPU. Speed depends on hardware, images and description length. Start with Q4 and a small batch. Disable photo descriptions, reduce frames, or turn off AI description for time/place-only naming. Stop finishes the current file and saves a partial plan; exiting requests an inference abort before releasing resources.

- **It says model files are incomplete.**
  Select a directory containing the matching GGUF main model and mmproj. Ollama blob directories cannot be used directly. If duplicate model pairs exist, select their specific subdirectory. Model manager shows missing/duplicate file reasons.

- **Can it damage my originals?**
  It only changes file names — no re-encoding, no compression, the picture is untouched. `Write file metadata` only **adds** description fields; your own captions, capture time, GPS and camera model are never deleted or changed.

- **I changed the language and nothing happened.**
  Language takes effect after restarting the app.

- **Will running the same folder twice duplicate things?**
  Already renamed files are skipped by default (tick `Re-process already renamed` to redo them). Metadata and `.ai.txt` overwrite the same spot, so nothing piles up.

## 7. Privacy

- Recognition and renaming happen entirely on your computer. Photos and videos do not leave the machine, and results are not uploaded.
- `Online precise location` is **off** by default: while off, the app sends no network requests and all place names come from the built-in offline database.
- With `Online precise location` on, only **latitude and longitude** are sent to the map provider to resolve an address — never the file itself.
- There are no in-app model downloads in this version. Prepare local GGUF files first; recognition works offline with online precise location disabled.

## 8. About

- 显影 SeeName
- 看见，即命名。/ See it. Name it.
- By tanhua
- Version %VER%
- Place data © OpenStreetMap contributors (ODbL) · world cities from GeoNames (CC-BY 4.0)
- Online lookups: Amap (lbs.amap.com) in China · Nominatim / OpenStreetMap elsewhere
