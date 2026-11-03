import glob, os, re
import cv2, numpy as np
from PySide6.QtCore import Qt, QTimer, QRect, Signal
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget, QDialog, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QGridLayout, QSlider,
                               QSpinBox, QCheckBox, QTabWidget, QTableWidget, QLineEdit, QDoubleSpinBox, QFormLayout,
                               QScrollArea, QListWidget, QSizePolicy)
import shutil
from PySide6.QtWidgets import QFileDialog, QInputDialog, QComboBox, QCompleter
import json
import minimap
from config import MOB_DIR, TPL_DIR, WZ_DIR, LIB_DIR
from wz_import import import_sprites


def to_pixmap(bgr):
    h, w = bgr.shape[:2]
    return QPixmap.fromImage(QImage(np.ascontiguousarray(bgr).data, w, h, 3 * w, QImage.Format_BGR888).copy())


# ---------------------------------------------------------------- snapshot dialog (frozen frame, zoom, draw)
class Canvas(QWidget):
    changed = Signal()

    def __init__(self, frame, mode):
        super().__init__()
        self.mode, self.zoom, self.boxes, self.cur = mode, 3, [], None
        self.set_frame(frame)

    def set_frame(self, frame):
        self.frame, self.pix = frame, to_pixmap(frame)
        self.set_zoom(self.zoom)

    def set_zoom(self, z):
        self.zoom = int(np.clip(z, 1, 12))
        self.setFixedSize(self.pix.width() * self.zoom, self.pix.height() * self.zoom)
        self.update()

    def _pt(self, e):
        p = e.position()
        return int(np.clip(p.x() // self.zoom, 0, self.pix.width() - 1)), int(np.clip(p.y() // self.zoom, 0, self.pix.height() - 1))

    def mousePressEvent(self, e):
        x, y = self._pt(e); self.cur = [x, y, x, y]

    def mouseMoveEvent(self, e):
        if self.cur:
            self.cur[2:] = self._pt(e); self.update()

    def mouseReleaseEvent(self, e):
        if self.cur:
            if self.mode in ("name", "hp", "mp", "mm"):
                self.boxes = []
            self.boxes.append(self.cur); self.cur = None
            self.update(); self.changed.emit()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.zoom + (1 if e.angleDelta().y() > 0 else -1))
        else:
            e.ignore()

    def paintEvent(self, _):
        p = QPainter(self); p.scale(self.zoom, self.zoom); p.drawPixmap(0, 0, self.pix)
        for b in self.boxes + ([self.cur] if self.cur else []):
            p.setPen(QPen(QColor(0, 255, 0) if b is not self.cur else QColor(255, 255, 0), 0))
            if self.mode == "measure":
                p.drawLine(b[0], b[1], b[2], b[3])
            else:
                p.drawRect(QRect(min(b[0], b[2]), min(b[1], b[3]), abs(b[2] - b[0]), abs(b[3] - b[1])))


class Snapshot(QDialog):
    TITLES = {"name": "Draw a box around your NAME TAG", "mob": "Draw a box around each monster part/frame (then Save)",
              "measure": "Drag to measure (px)", "mm": "Draw a box around the MINIMAP PICTURE only (not the title bar)", "hp": "Draw over the RED HP bar fill", "mp": "Draw over the BLUE MP bar fill"}

    def __init__(self, parent, mode, get_frame, on_save):
        super().__init__(parent)
        self.mode, self.get_frame, self.on_save = mode, get_frame, on_save
        self.setWindowTitle(self.TITLES[mode])
        self.canvas = Canvas(get_frame(), mode)
        self.canvas.changed.connect(self.on_change)
        sc = QScrollArea(); sc.setWidget(self.canvas)

        def btn(t, f):
            b = QPushButton(t); b.clicked.connect(f); return b
        bar = QHBoxLayout()
        for t, f in (("Zoom -", lambda: self.canvas.set_zoom(self.canvas.zoom - 1)), ("Zoom +", lambda: self.canvas.set_zoom(self.canvas.zoom + 1)),
                     ("Undo [U]", self.undo), ("Clear [C]", self.clear), ("Refresh screen [F5]", self.refresh)):
            bar.addWidget(btn(t, f))
        bar.addStretch(); bar.addWidget(QLabel(self.TITLES[mode] + "   (Ctrl+wheel = zoom)"))
        for key, f in (("U", self.undo), ("C", self.clear), ("F5", self.refresh)):
            QShortcut(QKeySequence(key), self, activated=f)

        bottom = QHBoxLayout()
        self.name = QLineEdit(); self.name.setPlaceholderText("Monster name, e.g. snail_head")
        self.log = QListWidget(); self.log.setMaximumHeight(90)
        if mode == "mob":
            bottom.addWidget(self.name)
        if mode == "measure":
            bottom.addWidget(self.log)
        else:
            bottom.addWidget(btn("SAVE", self.save))
        bottom.addWidget(btn("Done", self.accept))
        lay = QVBoxLayout(self); lay.addLayout(bar); lay.addWidget(sc); lay.addLayout(bottom)

    def on_change(self):
        if self.mode == "measure":
            b = self.canvas.boxes[-1]; dx, dy = b[2] - b[0], b[3] - b[1]
            self.log.addItem(f"{np.hypot(dx, dy):.1f}px   dx {dx}   dy {dy}")

    def undo(self):
        if self.canvas.boxes: self.canvas.boxes.pop(); self.canvas.update()

    def clear(self):
        self.canvas.boxes = []; self.canvas.update()

    def refresh(self):
        f = self.get_frame()
        if f is not None: self.canvas.set_frame(f); self.clear()

    def save(self):
        boxes = [(min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])) for b in self.canvas.boxes]
        boxes = [b for b in boxes if b[2] - b[0] >= 3 and b[3] - b[1] >= 3]
        if not boxes:
            return
        if self.mode == "mob" and not self.name.text().strip():
            self.name.setPlaceholderText("<- type a name first"); return
        self.on_save(self.mode, boxes, self.name.text(), self.canvas.frame)
        if self.mode == "mob":
            self.clear()
        else:
            self.accept()


# ---------------------------------------------------------------- studio window
class Studio(QWidget):
    def __init__(self, cfg, worker, analyzer, nav=None):
        super().__init__()
        self.setWindowTitle("Pixel Studio")
        self.cfg, self.worker, self.an, self.nav = cfg, worker, analyzer, nav
        self.view = QLabel(); self.view.setAlignment(Qt.AlignCenter); self.view.setStyleSheet("background:#000")
        self.view.setMinimumSize(640, 420); self.view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.status = QLabel("")

        grid = QGridLayout()
        for i, (txt, mode) in enumerate([("Name Tracking", "name"), ("Capture Img (mob)", "mob"), ("Measure Tool", "measure"),
                                         ("Vitals: HP bar", "hp"), ("Vitals: MP bar", "mp"), ("Minimap box", "mm"), ("Re-align minimap", "align"), ("Import wz sprites", "import"), ("Save config", None)]):
            b = QPushButton(txt); b.clicked.connect(lambda _=False, m=mode: self.import_wz() if m == "import" else (self.realign() if m == "align" else (self.open_snap(m) if m else self.save_cfg())))
            grid.addWidget(b, i // 2, i % 2)

        tabs = QTabWidget()
        tabs.addTab(self._monsters_tab(), "Monsters"); tabs.addTab(self._range_tab(), "Attack & Detection"); tabs.addTab(self._vitals_tab(), "Vitals / Keys")
        right = QVBoxLayout(); right.addLayout(grid)
        right.addWidget(self._row("Name threshold %", 50, 99, int(cfg.name_threshold * 100), lambda v: setattr(cfg, "name_threshold", v / 100)))
        right.addWidget(self._row("Name->body offset Y", -150, 50, cfg.name_offset_y, lambda v: setattr(cfg, "name_offset_y", v)))
        self.name_img = QLabel(); self.name_img.setAlignment(Qt.AlignCenter); self.name_img.setStyleSheet("background:#111;border:1px solid #444")
        self.name_img.setMinimumHeight(48)
        self.name_txt = QLabel("Name tracking: no template"); self._nk = None
        right.addWidget(self.name_img); right.addWidget(self.name_txt)
        right.addWidget(self._map_picker())
        right.addWidget(tabs)
        side = QWidget(); side.setLayout(right); side.setFixedWidth(380)
        row = QHBoxLayout(); row.addWidget(self.view, 3); row.addWidget(side)
        outer = QVBoxLayout(self); outer.addLayout(row); outer.addWidget(self.status)

        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(33)
        self.refresh_mobs()

    # ----- widgets
    def _row(self, label, lo, hi, val, setter):
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        s = QSlider(Qt.Horizontal); s.setRange(lo, hi); s.setValue(val)
        sp = QSpinBox(); sp.setRange(lo, hi); sp.setValue(val)
        s.valueChanged.connect(sp.setValue); sp.valueChanged.connect(s.setValue); sp.valueChanged.connect(setter)
        h.addWidget(QLabel(label)); h.addWidget(s); h.addWidget(sp)
        return w

    def _map_picker(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0)
        self.map_box = QComboBox(); self.map_box.setEditable(True); self.map_box.setInsertPolicy(QComboBox.NoInsert)
        db = self.an.mapdb()
        for k, m in sorted(db.items(), key=lambda kv: (kv[1].get("name", ""), kv[0])):
            self.map_box.addItem(f"{m.get('name') or '?'} - {m.get('street') or '?'}  [{k}]", k)
        comp = self.map_box.completer(); comp.setFilterMode(Qt.MatchContains); comp.setCaseSensitivity(Qt.CaseInsensitive)
        i = self.map_box.findData(self.cfg.current_map)
        self.map_box.setCurrentIndex(i)
        if i < 0:
            self.map_box.setEditText("")
        self.map_box.activated.connect(self.on_map)
        self.spawn_lbl = QLabel(); self.spawn_lbl.setWordWrap(True)
        v.addWidget(QLabel("Current map (search by name):" if db else "mapdb.json not found next to main.py"))
        v.addWidget(self.map_box); v.addWidget(self.spawn_lbl)
        self._spawn_text()
        return w

    def _spawn_text(self):
        m = self.an.mapdb().get(self.cfg.current_map)
        if not m:
            self.spawn_lbl.setText("Pick a map so the bot knows which monsters to look for."); return
        try:
            lib = json.load(open(os.path.join(LIB_DIR, "library.json"), encoding="utf-8"))
        except Exception:
            lib = {}
        byid = {v["id"]: (k, v["name"]) for k, v in lib.items()}
        parts = []
        for i in sorted({int(x["id"]) for x in m["mobs"]}):
            nm = byid.get(i, (None, "?"))[1]
            parts.append(f"{nm} ({i})" + ("" if i in byid else " - no sprites in library"))
        self.spawn_lbl.setText("Spawns here: " + (", ".join(parts) or "none"))

    def on_map(self, idx):
        self.cfg.current_map = self.map_box.itemData(idx)
        self.an.load_templates(); self.refresh_mobs(); self._spawn_text(); self.cfg.save()
        self.status.setText(f"Map set: {self.map_box.currentText()}")

    def _monsters_tab(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(["On", "Name", "Thr", "Del"])
        v.addWidget(self.table)
        cb = QCheckBox("Auto-match mirrored sprites (left/right)"); cb.setChecked(self.cfg.mob_flip)
        cb.toggled.connect(lambda x: (setattr(self.cfg, "mob_flip", x), self.an.load_templates())); v.addWidget(cb)
        pm = QCheckBox("Match partial sprites (mob hidden behind loot) - slower scan"); pm.setChecked(self.cfg.part_match)
        pm.toggled.connect(lambda x: (setattr(self.cfg, "part_match", x), self.an.load_templates())); v.addWidget(pm)
        v.addWidget(self._row("Keep lost mob (x0.1s)", 0, 30, int(self.cfg.track_ttl * 10), lambda x: setattr(self.cfg, "track_ttl", x / 10)))
        return w

    def _range_tab(self):
        c = self.cfg
        w = QWidget(); v = QVBoxLayout(w)
        for lbl, lo, hi, k in (("Basic reach", 10, 800, "attack_reach"), ("Basic height", 10, 400, "attack_height")):
            v.addWidget(self._row(lbl, lo, hi, getattr(c, k), lambda x, k=k: setattr(c, k, x)))
        cb = QCheckBox("Use skill attack (ranged classes)"); cb.setChecked(c.skill_enabled)
        cb.toggled.connect(lambda x: setattr(c, "skill_enabled", x)); v.addWidget(cb)
        for lbl, lo, hi, k in (("Skill reach", 10, 1200, "skill_reach"), ("Detect extra W", 0, 1500, "det_extra_w"), ("Detect extra H", 0, 600, "det_extra_h")):
            v.addWidget(self._row(lbl, lo, hi, getattr(c, k), lambda x, k=k: setattr(c, k, x)))
        v.addStretch()
        return w

    def _vitals_tab(self):
        c = self.cfg
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(self._row("HP potion below %", 0, 100, c.hp_pct, lambda x: setattr(c, "hp_pct", x)))
        v.addWidget(self._row("MP potion below %", 0, 100, c.mp_pct, lambda x: setattr(c, "mp_pct", x)))
        self.vit_lbl = QLabel("HP: -   MP: -"); v.addWidget(self.vit_lbl)
        f = QFormLayout()
        for k in ["left", "right", "attack", "skill", "hp", "mp"]:
            e = QLineEdit(c.keys[k]); e.setMaximumWidth(120)
            e.textChanged.connect(lambda t, k=k: c.keys.__setitem__(k, t.strip().lower())); f.addRow(f"{k} key", e)
        v.addLayout(f); v.addStretch()
        return w

    def refresh_mobs(self):
        self.table.setRowCount(0)
        for name in sorted(self.an.mob_counts):
            m = self.cfg.monsters[name]
            r = self.table.rowCount(); self.table.insertRow(r)
            cb = QCheckBox(); cb.setChecked(m["on"]); cb.toggled.connect(lambda x, n=name: self.cfg.monsters[n].__setitem__("on", x))
            sp = QDoubleSpinBox(); sp.setRange(0.3, 0.99); sp.setSingleStep(0.01); sp.setValue(m["thr"])
            sp.valueChanged.connect(lambda x, n=name: self.cfg.monsters[n].__setitem__("thr", x))
            d = QPushButton("Del"); d.clicked.connect(lambda _=False, n=name: self.del_mob(n))
            self.table.setCellWidget(r, 0, cb); self.table.setCellWidget(r, 1, QLabel(f" {name} ({self.an.mob_counts[name]})"))
            self.table.setCellWidget(r, 2, sp); self.table.setCellWidget(r, 3, d)

    def del_mob(self, name):
        for f in glob.glob(os.path.join(MOB_DIR, f"{name}_*.png")):
            os.remove(f)
        shutil.rmtree(os.path.join(WZ_DIR, name), ignore_errors=True)
        self.cfg.monsters.pop(name, None); self.an.load_templates(); self.refresh_mobs()

    def import_wz(self):
        src = QFileDialog.getExistingDirectory(self, "Folder of PNGs exported from wzpy for ONE monster")
        if not src:
            return
        name, ok = QInputDialog.getText(self, "Monster", "Name for this monster (e.g. snail):")
        if not ok or not name.strip():
            return
        name, counts, no_alpha = import_sprites(src, name)
        self.cfg.monsters.pop(name, None); self.an.load_templates(); self.refresh_mobs(); self.cfg.save()
        self.status.setText(f"Imported '{name}': {counts or 'NOTHING FOUND'}" + (f"  ({no_alpha} PNGs had no alpha, skipped)" if no_alpha else ""))

    def realign(self):
        self.an.realign()
        self.status.setText("Re-aligning the minimap picture to the map data ...")

    def _explore(self, img, frame):
        """Draw the zoomed 'exploration map' (minimap picture + platforms/ropes/route/zone) into the top-right of the live view."""
        c, nav, H, W = self.cfg, self.nav, img.shape[0], img.shape[1]
        m = self.an.mapdb().get(c.current_map)
        if nav is None or not (c.mm_roi and m and (m.get("minimap") or {}).get("mag") is not None):
            msg = "EXPLORATION MAP: pick the map + draw the Minimap box"
            cv2.rectangle(img, (W - 330, 4), (W - 4, 26), (40, 40, 40), -1)
            cv2.putText(img, msg, (W - 324, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 200, 255), 1)
            return
        nav.sync()
        x1, y1, x2, y2 = c.mm_roi
        crop = frame[y1:y2, x1:x2]
        if nav.nm is None or crop.size == 0:
            return
        zoom = int(np.clip(round(W * 0.48 / max(1, crop.shape[1])), 1, 4))
        legs = ([nav.leg] if nav.leg else []) + list(nav.legs)
        ex = minimap.render_explore(crop, m, nav.nm, self.an.mm_off, zoom, est=(nav.x, nav.y) if nav.x is not None else None,
                                    zone=nav.zone(), legs=legs, cur_pid=nav.cur.id if nav.cur else None, aligned=self.an.mm_conf >= 0.25)
        h, w = ex.shape[:2]
        if h < H - 8 and w < W - 8:
            img[4:4 + h, W - w - 4:W - 4] = ex
            cv2.rectangle(img, (W - w - 4, 4), (W - 4, 4 + h), (200, 200, 200), 1)

    def save_cfg(self):
        self.cfg.save(); self.status.setText("Config saved.")

    # ----- snapshot workflow
    def _frame(self):
        lt = self.worker.latest
        return None if not lt or lt[0] is None else lt[0].copy()

    def open_snap(self, mode):
        if self._frame() is None:
            self.status.setText("Game window not found - open MapleStory (windowed) and press Detect Game."); return
        dlg = Snapshot(self, mode, self._frame, self.on_snap_save); dlg.resize(1150, 760); dlg.exec()

    def on_snap_save(self, mode, boxes, name, frame):
        crop = lambda b: frame[b[1]:b[3], b[0]:b[2]]
        if mode == "name":
            cv2.imwrite(os.path.join(TPL_DIR, "name.png"), crop(boxes[0]))
        elif mode == "mob":
            name = re.sub(r"[^A-Za-z0-9]", "", name.strip()) or "mob"
            used = [int(m.group(1)) for f in glob.glob(os.path.join(MOB_DIR, f"{name}_*.png"))
                    if (m := re.search(r"_(\d+)\.png$", f))]
            n = max(used, default=0)
            for b in boxes:
                n += 1; cv2.imwrite(os.path.join(MOB_DIR, f"{name}_{n}.png"), crop(b))
        elif mode in ("hp", "mp", "mm"):
            setattr(self.cfg, f"{mode}_roi", list(boxes[0]))
        self.an.load_templates(); self.refresh_mobs(); self.cfg.save()
        self.status.setText(f"Saved {mode}.")

    # ----- live view (reads the worker's latest frame; no heavy work here)
    def tick(self):
        lt = self.worker.latest
        if not lt or lt[0] is None:
            self.status.setText("PAUSED - " + (self.worker.cap.reason or "game window not found") + "  (bring MapleStory fully into view)")
            self.name_txt.setText("<span style='color:#e90'>Name tracking: waiting for the game window</span>"); return
        frame, a, _ = lt
        img, c = frame.copy(), self.cfg
        if a["player"]:
            px, py = a["player"]; ah = c.attack_height // 2
            cv2.drawMarker(img, (px, py), (0, 255, 255), cv2.MARKER_CROSS, 14, 2)
            for s in (1, -1):
                cv2.rectangle(img, (px, py - ah), (px + s * c.attack_reach, py + ah), (0, 128, 255), 1)
                if c.skill_enabled:
                    cv2.rectangle(img, (px, py - ah), (px + s * c.skill_reach, py + ah), (200, 80, 200), 1)
            rw = (max(c.attack_reach, c.skill_reach) if c.skill_enabled else c.attack_reach) + c.det_extra_w
            rh = ah + c.det_extra_h
            cv2.rectangle(img, (px - rw, py - rh), (px + rw, py + rh), (255, 160, 0), 1)
        for m in a["mobs"]:
            x, y, w, h = int(m["x"]), int(m["y"]), m["w"], m["h"]
            col = (0, 255, 0) if m["fresh"] else (0, 200, 255)       # green = seen now, yellow = remembered
            cv2.rectangle(img, (x - w // 2, y - h // 2), (x + w // 2, y + h // 2), col, 1)
            cv2.putText(img, f"{m['name']} {m['sc']:.2f}", (x - w // 2, y - h // 2 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
        for key, col in (("hp_roi", (0, 0, 255)), ("mp_roi", (255, 120, 0))):
            r = getattr(c, key)
            if r: cv2.rectangle(img, (r[0], r[1]), (r[2], r[3]), col, 1)
        tpl = self.an.name_tpl
        if tpl is None:
            self.name_txt.setText("<span style='color:#e90'>Name tracking: no template - click Name Tracking</span>")
        else:
            if self._nk is not id(tpl):
                self._nk = id(tpl)
                self.name_img.setPixmap(to_pixmap(cv2.cvtColor(tpl, cv2.COLOR_GRAY2BGR)).scaled(330, 44, Qt.KeepAspectRatio, Qt.FastTransformation))
            ok = a["player"] is not None
            self.name_txt.setText(f"<span style='color:{'#3c3' if ok else '#e44'}'><b>Name tracking: {'FOUND' if ok else 'LOST'}</b></span> "
                                  f"score {a['name_score']:.2f} (threshold {c.name_threshold:.2f})")
        if c.mm_roi:
            r = c.mm_roi; cv2.rectangle(img, (r[0], r[1]), (r[2], r[3]), (255, 0, 255), 1)
            if a.get("mm"):
                cv2.circle(img, (int(a["mm"][2]), int(a["mm"][3])), 5, (0, 255, 255), 1)
        self._explore(img, frame)
        f = lambda v: "-" if v is None else f"{v*100:.0f}%"
        self.vit_lbl.setText(f"HP: {f(a['hp'])}   MP: {f(a['mp'])}   name score {a['name_score']:.2f}")
        mmtxt = "" if not c.mm_roi else (f"   |   minimap: {'aligned %.2f' % self.an.mm_conf if self.an.mm_conf >= 0.25 else 'NOT aligned (%.2f)' % self.an.mm_conf}, "
                                        f"dot {'found' if a.get('mm') else 'MISSING'}")
        self.status.setText(f"vision {self.worker.fps:.0f} fps{mmtxt}")
        self.view.setPixmap(to_pixmap(img).scaled(self.view.size(), Qt.KeepAspectRatio, Qt.FastTransformation))

    def closeEvent(self, e):
        self.cfg.save(); super().closeEvent(e)
