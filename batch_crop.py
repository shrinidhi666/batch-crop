"""Batch Crop: pick one image, drag a crop box (locked to center + aspect), apply to the checked images in the folder.
Crop width/height snap to a multiple (default 32) for video-generation models.

Controls:
  - Drag a corner of the box  -> resize (keeps aspect, grows from center)
  - Mouse wheel               -> resize
  - Drag inside the box       -> move (only when "Lock to center" is off)
  - Left / Right arrow        -> preview the same crop on other images in the folder
  - Checkboxes in the file list pick which images get cropped
  - Drop an image or folder onto the window to open it
"""
import sys
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QImageReader, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QCheckBox, QSlider, QFileDialog, QProgressBar, QMessageBox, QSpinBox,
    QListWidget, QListWidgetItem,
)

EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
ASPECTS = [
    ("Same as image", None), ("16:9", 16 / 9), ("9:16", 9 / 16), ("1:1", 1.0),
    ("4:3", 4 / 3), ("3:4", 3 / 4), ("4:5", 4 / 5), ("21:9", 21 / 9),
]
HANDLE = 14  # px grab radius for corners


def crop_box_f(w, h, aspect, cx, cy, s):
    """Crop box in image pixels (floats). cx/cy are normalized center, s is fraction of the largest box that fits."""
    a = aspect or w / h
    if w / h > a:
        mw, mh = h * a, h
    else:
        mw, mh = w, w / a
    bw, bh = mw * s, mh * s
    x = min(max(cx * w - bw / 2, 0), w - bw)
    y = min(max(cy * h - bh / 2, 0), h - bh)
    return x, y, bw, bh


def snap(v, m, limit):
    """Round v to the nearest multiple of m, at least m, at most the largest multiple that fits in limit."""
    top = limit // m * m or limit
    return min(max(m, round(v / m) * m), top)


def crop_box_i(w, h, aspect, cx, cy, s, mult=1):
    """Integer crop box; width and height snapped to a multiple of `mult`, kept on the same center."""
    x, y, bw, bh = crop_box_f(w, h, aspect, cx, cy, s)
    mx, my = x + bw / 2, y + bh / 2
    bw, bh = snap(bw, mult, w), snap(bh, mult, h)
    x, y = min(max(round(mx - bw / 2), 0), w - bw), min(max(round(my - bh / 2), 0), h - bh)
    return x, y, bw, bh


OUTPUTS = ["PNG (uncompressed)", "TIFF (uncompressed)"]


def save_uncompressed(im, src, dst, out_fmt):
    """Save the cropped pixels with zero compression, carrying over metadata
    (ICC color profile, EXIF, DPI; PNG text chunks). Returns the path written."""
    info = src.info
    kw = {k: info[k] for k in ("icc_profile", "dpi") if info.get(k)}
    exif = im.info.get("exif") or info.get("exif")
    if exif:
        kw["exif"] = exif

    if out_fmt == "TIFF":
        if im.mode == "P":
            im = im.convert("RGBA" if "transparency" in info else "RGB")
        dst = dst.with_suffix(".tif")
        im.save(dst, "TIFF", compression="raw", **kw)
        return dst

    from PIL.PngImagePlugin import PngInfo
    meta = PngInfo()
    for k, v in (getattr(src, "text", None) or {}).items():
        meta.add_text(k, v)
    if "transparency" in info:
        kw["transparency"] = info["transparency"]
    dst = dst.with_suffix(".png")
    im.save(dst, "PNG", pnginfo=meta, compress_level=0, **kw)
    return dst


def list_images(folder):
    return sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in EXTS)


class Canvas(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.pix = None
        self.aspect = None
        self.mult = 32
        self.cx, self.cy, self.s = 0.5, 0.5, 0.8
        self.lock = True
        self.drag = None
        self.drag_off = QPointF()
        self.setMouseTracking(True)
        self.setMinimumSize(700, 450)

    # --- geometry ---------------------------------------------------------
    def img_size(self):
        return self.pix.width(), self.pix.height()

    def view_rect(self):
        w, h = self.img_size()
        m = 12
        k = min((self.width() - 2 * m) / w, (self.height() - 2 * m) / h)
        vw, vh = w * k, h * k
        return QRectF((self.width() - vw) / 2, (self.height() - vh) / 2, vw, vh), k

    def box(self):
        w, h = self.img_size()
        return crop_box_i(w, h, self.aspect, self.cx, self.cy, self.s, self.mult)

    def box_view(self):
        vr, k = self.view_rect()
        x, y, bw, bh = self.box()
        return QRectF(vr.x() + x * k, vr.y() + y * k, bw * k, bh * k)

    def to_img(self, p):
        vr, k = self.view_rect()
        return QPointF((p.x() - vr.x()) / k, (p.y() - vr.y()) / k)

    def normalize(self):
        """Re-derive center from the clamped box so the stored center never sits outside the image."""
        w, h = self.img_size()
        x, y, bw, bh = self.box()
        self.cx, self.cy = (x + bw / 2) / w, (y + bh / 2) / h

    def set_scale(self, s):
        self.s = min(max(s, 0.05), 1.0)
        if self.lock:
            self.cx, self.cy = 0.5, 0.5
        self.normalize()
        self.update()
        self.changed.emit()

    # --- events -----------------------------------------------------------
    def near_corner(self, p):
        r = self.box_view()
        return any((p - c).manhattanLength() < HANDLE * 1.5
                   for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()))

    def mousePressEvent(self, e):
        if not self.pix or e.button() != Qt.LeftButton:
            return
        p = e.position()
        if self.near_corner(p):
            self.drag = "resize"
        elif self.box_view().contains(p) and not self.lock:
            self.drag = "move"
            x, y, bw, bh = self.box()
            self.drag_off = self.to_img(p) - QPointF(x + bw / 2, y + bh / 2)

    def mouseMoveEvent(self, e):
        if not self.pix:
            return
        p = e.position()
        if self.drag == "resize":
            w, h = self.img_size()
            full = crop_box_f(w, h, self.aspect, 0.5, 0.5, 1.0)
            x, y, bw, bh = self.box()
            m = self.to_img(p)
            sx = abs(m.x() - (x + bw / 2)) * 2 / full[2]
            sy = abs(m.y() - (y + bh / 2)) * 2 / full[3]
            self.set_scale(max(sx, sy))
        elif self.drag == "move":
            w, h = self.img_size()
            c = self.to_img(p) - self.drag_off
            self.cx, self.cy = c.x() / w, c.y() / h
            self.normalize()
            self.update()
            self.changed.emit()
        else:
            if self.near_corner(p):
                self.setCursor(Qt.SizeFDiagCursor)
            elif self.box_view().contains(p) and not self.lock:
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        self.drag = None

    def wheelEvent(self, e):
        if self.pix:
            self.set_scale(self.s * 1.03 ** (e.angleDelta().y() / 120))

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(28, 28, 30))
        if not self.pix:
            p.setPen(QColor(160, 160, 160))
            p.drawText(self.rect(), Qt.AlignCenter, "Open an image (or drop one here)")
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        vr, _ = self.view_rect()
        p.drawPixmap(vr, self.pix, QRectF(self.pix.rect()))

        br = self.box_view()
        shade = QPainterPath()
        shade.addRect(vr)
        hole = QPainterPath()
        hole.addRect(br)
        p.fillPath(shade - hole, QColor(0, 0, 0, 160))

        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        for i in (1, 2):
            p.drawLine(QPointF(br.x() + br.width() * i / 3, br.top()), QPointF(br.x() + br.width() * i / 3, br.bottom()))
            p.drawLine(QPointF(br.left(), br.y() + br.height() * i / 3), QPointF(br.right(), br.y() + br.height() * i / 3))
        p.setPen(QPen(QColor(255, 200, 0), 2))
        p.drawRect(br)
        p.setBrush(QColor(255, 200, 0))
        for c in (br.topLeft(), br.topRight(), br.bottomLeft(), br.bottomRight()):
            p.drawRect(QRectF(c.x() - 5, c.y() - 5, 10, 10))


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Batch Crop")
        self.setAcceptDrops(True)
        self.files, self.idx = [], 0

        self.canvas = Canvas()
        self.canvas.changed.connect(self.sync)

        open_btn = QPushButton("Open image…")
        open_btn.clicked.connect(self.open_dialog)
        self.aspect = QComboBox()
        for name, _ in ASPECTS:
            self.aspect.addItem(name)
        self.aspect.currentIndexChanged.connect(self.set_aspect)
        self.mult = QSpinBox()
        self.mult.setRange(1, 512)
        self.mult.setValue(32)
        self.mult.setToolTip("Crop width and height are snapped to a multiple of this (1 = no snapping)")
        self.mult.valueChanged.connect(self.set_mult)
        self.lock = QCheckBox("Lock to center")
        self.lock.setChecked(True)
        self.lock.toggled.connect(self.set_lock)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(5, 100)
        self.slider.setValue(80)
        self.slider.valueChanged.connect(lambda v: self.canvas.set_scale(v / 100))
        self.size_lbl = QLabel("80%")
        self.size_lbl.setMinimumWidth(40)

        top = QHBoxLayout()
        top.addWidget(open_btn)
        top.addWidget(QLabel("Aspect:"))
        top.addWidget(self.aspect)
        top.addWidget(QLabel("Multiple of:"))
        top.addWidget(self.mult)
        top.addWidget(self.lock)
        top.addWidget(QLabel("Size:"))
        top.addWidget(self.slider, 1)
        top.addWidget(self.size_lbl)

        # file list: checkbox = include in batch, click = preview
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.pick)
        self.list.itemChanged.connect(lambda _: self.sync())
        all_btn, none_btn = QPushButton("All"), QPushButton("None")
        all_btn.clicked.connect(lambda: self.check_all(True))
        none_btn.clicked.connect(lambda: self.check_all(False))
        btns = QHBoxLayout()
        btns.addWidget(QLabel("Images"))
        btns.addStretch(1)
        btns.addWidget(all_btn)
        btns.addWidget(none_btn)
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.addLayout(btns)
        side.addWidget(self.list, 1)
        side_w = QWidget()
        side_w.setLayout(side)
        side_w.setFixedWidth(260)
        mid = QHBoxLayout()
        mid.addWidget(side_w)
        mid.addWidget(self.canvas, 1)

        prev_btn, next_btn = QPushButton("◀"), QPushButton("▶")
        prev_btn.clicked.connect(lambda: self.step(-1))
        next_btn.clicked.connect(lambda: self.step(1))
        self.info = QLabel("")
        self.upscale = QCheckBox("Resize back to original size (resamples)")
        self.out_fmt = QComboBox()
        self.out_fmt.addItems(OUTPUTS)
        self.go = QPushButton("Crop")
        self.go.setEnabled(False)
        self.go.setStyleSheet("font-weight: bold; padding: 6px 18px;")
        self.go.clicked.connect(self.run)
        self.progress = QProgressBar()
        self.progress.setVisible(False)

        bottom = QHBoxLayout()
        bottom.addWidget(prev_btn)
        bottom.addWidget(next_btn)
        bottom.addWidget(self.info, 1)
        bottom.addWidget(QLabel("Output:"))
        bottom.addWidget(self.out_fmt)
        bottom.addWidget(self.upscale)
        bottom.addWidget(self.go)

        lay = QVBoxLayout()
        lay.addLayout(top)
        lay.addLayout(mid, 1)
        lay.addLayout(bottom)
        lay.addWidget(self.progress)
        w = QWidget()
        w.setLayout(lay)
        self.setCentralWidget(w)
        self.resize(1400, 850)

    # --- loading ----------------------------------------------------------
    def open_dialog(self):
        f, _ = QFileDialog.getOpenFileName(self, "Pick one image from the folder", "",
                                           "Images (" + " ".join("*" + x for x in EXTS) + ")")
        if f:
            self.open_path(Path(f))

    def open_path(self, path):
        folder = path if path.is_dir() else path.parent
        files = list_images(folder)
        if not files:
            QMessageBox.warning(self, "Batch Crop", f"No images in {folder}")
            return
        self.files = files
        self.list.blockSignals(True)
        self.list.clear()
        for f in files:
            it = QListWidgetItem(f.name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        row = files.index(path) if path in files else 0
        self.list.setCurrentRow(row)  # triggers pick() -> load()

    def pick(self, row):
        if 0 <= row < len(self.files):
            self.idx = row
            self.load()

    def load(self):
        r = QImageReader(str(self.files[self.idx]))
        r.setAutoTransform(True)
        self.canvas.pix = QPixmap.fromImage(r.read())
        self.canvas.normalize()
        self.canvas.update()
        self.sync()

    def step(self, d):
        if self.files:
            self.list.setCurrentRow((self.idx + d) % len(self.files))

    def checked(self):
        return [f for i, f in enumerate(self.files) if self.list.item(i).checkState() == Qt.Checked]

    def check_all(self, on):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.list.blockSignals(False)
        self.sync()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Left:
            self.step(-1)
        elif e.key() == Qt.Key_Right:
            self.step(1)
        else:
            super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.open_path(Path(e.mimeData().urls()[0].toLocalFile()))

    # --- controls ---------------------------------------------------------
    def refresh(self):
        if self.canvas.pix:
            self.canvas.set_scale(self.canvas.s)

    def set_aspect(self, i):
        self.canvas.aspect = ASPECTS[i][1]
        self.refresh()

    def set_mult(self, v):
        self.canvas.mult = v
        self.refresh()

    def set_lock(self, on):
        self.canvas.lock = on
        self.refresh()

    def sync(self):
        c = self.canvas
        self.slider.blockSignals(True)
        self.slider.setValue(round(c.s * 100))
        self.slider.blockSignals(False)
        self.size_lbl.setText(f"{round(c.s * 100)}%")
        if not c.pix:
            return
        w, h = c.img_size()
        x, y, bw, bh = c.box()
        f = self.files[self.idx]
        self.info.setText(f"{self.idx + 1}/{len(self.files)}  {f.name}   {w}×{h}  →  crop {bw}×{bh} at ({x}, {y})")
        n = len(self.checked())
        self.go.setText(f"Crop {n} image{'' if n == 1 else 's'}")
        self.go.setEnabled(n > 0)

    # --- batch ------------------------------------------------------------
    def run(self):
        c = self.canvas
        files = self.checked()
        m = c.mult
        out = self.files[0].parent / "cropped"
        out.mkdir(exist_ok=True)
        self.progress.setRange(0, len(files))
        self.progress.setVisible(True)
        self.go.setEnabled(False)
        errors = []
        for i, f in enumerate(files):
            try:
                with Image.open(f) as src:
                    im = ImageOps.exif_transpose(src)
                    w, h = im.size
                    x, y, bw, bh = crop_box_i(w, h, c.aspect, c.cx, c.cy, c.s, m)
                    im = im.crop((x, y, x + bw, y + bh))
                    if self.upscale.isChecked():
                        k = min(w / bw, h / bh)
                        im = im.resize((max(m, int(bw * k) // m * m), max(m, int(bh * k) // m * m)), Image.LANCZOS)
                    save_uncompressed(im, src, out / f.name, self.out_fmt.currentText().split()[0])
            except Exception as ex:
                errors.append(f"{f.name}: {ex}")
            self.progress.setValue(i + 1)
            QApplication.processEvents()
        self.go.setEnabled(True)
        self.progress.setVisible(False)
        msg = f"Cropped {len(files) - len(errors)} images into:\n{out}"
        if errors:
            msg += "\n\nFailed:\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Batch Crop", msg)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = Main()
    win.show()
    if len(sys.argv) > 1:
        win.open_path(Path(sys.argv[1]))
    sys.exit(app.exec())
