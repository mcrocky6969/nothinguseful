import time
import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QSlider, QSpinBox, QSizePolicy
from studio import to_pixmap


class NavWindow(QWidget):
    """Exploration map: platforms (green), ropes (red), you (cyan ring), hunting zone (yellow). Drag on the map to set the zone."""

    def __init__(self, cfg, worker, nav, bot):
        super().__init__()
        self.setWindowTitle("Navigation - Pixel Studio")
        self.cfg, self.worker, self.nav, self.bot = cfg, worker, nav, bot
        self.tf, self.drag = None, None
        self.view = QLabel(); self.view.setStyleSheet("background:#1e1e1e"); self.view.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.view.setMinimumSize(760, 440); self.view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.view.mousePressEvent, self.view.mouseMoveEvent, self.view.mouseReleaseEvent = self._press, self._move, self._release
        self.info = QLabel(); self.info.setWordWrap(True); self.info.setMinimumWidth(300)
        side = QVBoxLayout()
        side.addWidget(QLabel("<b>Hunting zone</b>: drag a box on the map around the platform line(s) to farm."))
        b = QPushButton("Clear hunting zone"); b.clicked.connect(self.clear_zone); side.addWidget(b)
        for lbl, lo, hi, k in (("Walk speed px/s", 50, 300, "walk_speed"), ("Jump height px", 30, 120, "jump_h"), ("Jump distance px", 40, 220, "jump_dx")):
            side.addWidget(self._row(lbl, lo, hi, k))
        side.addWidget(self.info); side.addStretch()
        panel = QWidget(); panel.setLayout(side); panel.setFixedWidth(330)
        lay = QHBoxLayout(self); lay.addWidget(self.view, 1); lay.addWidget(panel)
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(100)

    def _row(self, label, lo, hi, key):
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        s = QSlider(Qt.Horizontal); s.setRange(lo, hi); s.setValue(getattr(self.cfg, key))
        sp = QSpinBox(); sp.setRange(lo, hi); sp.setValue(getattr(self.cfg, key))
        s.valueChanged.connect(sp.setValue); sp.valueChanged.connect(s.setValue); sp.valueChanged.connect(lambda v: (setattr(self.cfg, key, v), self.cfg.save()))
        h.addWidget(QLabel(label)); h.addWidget(s); h.addWidget(sp)
        return w

    # ---- zone drawing (widget px -> world)
    def _world(self, e):
        if not self.tf:
            return None
        s, ox, oy = self.tf
        p = e.position()
        return ((p.x() - ox) / s, (p.y() - oy) / s)

    def _press(self, e):
        w = self._world(e)
        if w: self.drag = [w, w]

    def _move(self, e):
        w = self._world(e)
        if w and self.drag: self.drag[1] = w

    def _release(self, e):
        if not self.drag:
            return
        (x1, y1), (x2, y2) = self.drag; self.drag = None
        if abs(x2 - x1) > 20 and abs(y2 - y1) > 8 and self.nav.map_id:
            self.cfg.hunt[self.nav.map_id] = [round(min(x1, x2)), round(min(y1, y2)), round(max(x1, x2)), round(max(y1, y2))]
            self.cfg.save(); self.nav.legs, self.nav.leg = [], None

    def clear_zone(self):
        self.cfg.hunt.pop(self.nav.map_id, None); self.cfg.save(); self.nav.legs, self.nav.leg = [], None

    # ---- live drawing
    def tick(self):
        nav = self.nav
        nav.sync()
        lt = self.worker.latest
        if self.bot.state != "running" and lt and lt[0] is not None:     # keep the position live while the bot is stopped
            nav.update(lt[1], 0.0, 0.1, time.time())
        if nav.nm is None:
            self.view.setText("Pick your map in Pixel Studio first (Current map).")
            self.info.setText(""); return
        w, h = max(self.view.width(), 200), max(self.view.height(), 200)
        zone = None
        if self.drag:
            (x1, y1), (x2, y2) = self.drag; zone = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        zone = zone or nav.zone()
        legs = ([nav.leg] if nav.leg else []) + list(nav.legs)
        img, self.tf = nav.nm.render(w, h, dot=(nav.x, nav.y) if nav.x is not None else None, zone=zone, legs=legs,
                                     cur_pid=nav.cur.id if nav.cur else None)
        cv2.putText(img, f"{nav.status}", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        self.view.setPixmap(to_pixmap(img))
        m = nav.nm.m
        self.info.setText(
            f"<b>{m.get('name') or '?'}</b> ({m.get('street') or ''})<br>"
            f"{len(nav.nm.plats)} platforms, {len(nav.nm.ladders)} ropes/ladders<br>"
            f"minimap: {'dot found' if nav.mm_ok else '<span style=\"color:#e55\">dot NOT found - set the Minimap box in Studio</span>'}<br>"
            f"position: {'-' if nav.x is None else f'x {nav.x:.0f}, y {nav.y:.0f}'}  platform: {('P%d' % nav.cur.id) if nav.cur else '-'}<br>"
            f"hunting zone: {'set' if nav.zone() else '<span style=\"color:#e90\">not set - drag a box on the map</span>'}<br>"
            f"bot: {self.bot.status}")
