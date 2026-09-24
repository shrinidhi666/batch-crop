# Batch Crop

Crop a whole folder of images at the exact same position — visually, in one go.

Pick one image, size the crop box (locked to the center and to the image's aspect ratio), tick which images to include, click **Crop**. Width and height snap to a multiple of 32 (adjustable), ready for AI video-generation models.

## Download

Grab **`BatchCrop.exe`** from the [Releases](../../releases) page. It is a single standalone Windows executable — no Python, no installer, nothing else to set up. Just double-click it.

## Features

- **Center crop, same aspect** as the source image by default (16:9, 9:16, 1:1, 4:3 … also available)
- **Snap to multiple** — crop width/height always a multiple of N (default 32, set 1 to disable)
- **Pick images** — checkbox list of every image in the folder, with All / None
- **Preview** the same crop on any image before running
- **No quality loss** — output is uncompressed PNG or uncompressed TIFF; pixels are copied exactly. ICC color profile, EXIF, DPI and PNG text metadata are kept
- Optional **resize back to original size** (resamples, snapped to the same multiple)
- Originals are never touched — results go to a `cropped` subfolder

Supported input: PNG, JPEG, WebP, BMP, TIFF.

## Controls

| Action | How |
|---|---|
| Resize crop | Drag a corner, mouse wheel, or the Size slider |
| Move crop | Untick **Lock to center**, then drag inside the box |
| Preview other images | Click in the list, ◀ ▶ buttons, or Left / Right arrow keys |
| Open | **Open image…**, or drop an image / folder on the window |

## Run from source

```
pip install -r requirements.txt
python batch_crop.py
```

## Build the exe

Requires Python 3.13 and Visual Studio 2022 (C++ build tools). Run `build.bat` — it compiles with [Nuitka](https://nuitka.net) into a single standalone `dist\BatchCrop.exe`.

## License

[MIT](LICENSE)
