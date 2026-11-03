import sys, time
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTextEdit
from config import Config
from capture import Capture
from vision import Analyzer, VisionWorker
from navigator import Navigator
from bot import Bot
from studio import Studio
from navwin import NavWindow


class LogBridge(QObject):
    line = Signal(str)


class Main(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pixel Bot")
        self.resize(620, 480)
        self.cfg = Config.load()
        self.cap = Capture(self.cfg.window_title)
        self.an = Analyzer(self.cfg)
        self.worker = VisionWorker(self.cap, self.an); self.worker.start()
        self.bridge = LogBridge(); self.bridge.line.connect(self.append_log)
        emit = lambda s: self.bridge.line.emit(s)
        self.nav = Navigator(self.cfg, self.an, emit)
        self.bot = Bot(self.cfg, self.worker, self.cap, self.nav, emit)
        self.studio = self.navwin = None

        self.game_lbl, self.name_lbl = QLabel("Game: not detected"), QLabel("Name tracking: -")
        self.state_lbl, self.act_lbl = QLabel("State: stopped"), QLabel("Doing: -")
        self.log = QTextEdit(); self.log.setReadOnly(True)
        lay = QVBoxLayout(self)
        for w in (self.game_lbl, self.name_lbl, self.state_lbl, self.act_lbl):
            lay.addWidget(w)
        for buttons in ((("Detect Game", self.detect), ("Pixel Studio", self.open_studio), ("Navigation", self.open_nav)),
                        (("START", self.bot.start), ("PAUSE", self.bot.pause), ("STOP", self.bot.stop))):
            row = QHBoxLayout()
            for t, f in buttons:
                b = QPushButton(t); b.clicked.connect(f); row.addWidget(b)
            lay.addLayout(row)
        lay.addWidget(self.log)
        self.t = QTimer(self); self.t.timeout.connect(self.refresh); self.t.start(300)
        self.append_log("Detect Game -> Pixel Studio (name tag, minimap box, map) -> Navigation (hunting zone) -> START (run as administrator).")

    def append_log(self, s):
        self.log.append(f"[{time.strftime('%H:%M:%S')}] {s}")

    def detect(self):
        self.append_log("game window found" if self.cap.find() else "game window NOT found (title must contain 'MapleStory')")

    def open_studio(self):
        if self.studio is None:
            self.studio = Studio(self.cfg, self.worker, self.an)
        self.studio.resize(1200, 760); self.studio.show(); self.studio.raise_()

    def open_nav(self):
        if self.navwin is None:
            self.navwin = NavWindow(self.cfg, self.worker, self.nav, self.bot)
        self.navwin.resize(1150, 640); self.navwin.show(); self.navwin.raise_()

    def refresh(self):
        lt = self.worker.latest
        a = lt[1] if lt and lt[0] is not None else None
        self.game_lbl.setText("Game: visible" if a else f"Game: {self.cap.reason or 'not detected'}")
        if a is None:
            self.name_lbl.setText("Name tracking: -")
        else:
            ok = a["player"] is not None
            self.name_lbl.setText(f"<span style='color:{'#3c3' if ok else '#e44'}'><b>Name tracking: {'FOUND' if ok else 'LOST'}</b></span> score {a['name_score']:.2f}"
                                  f"   |   minimap dot: {'found' if a.get('mm') else '-'}")
        self.state_lbl.setText(f"State: {self.bot.state}   vision: {self.worker.fps:.0f} fps")
        self.act_lbl.setText(f"Doing: {self.bot.status}")

    def closeEvent(self, e):
        self.bot.stop(); self.worker.running = False; self.cfg.save(); super().closeEvent(e)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    m = Main(); m.show()
    sys.exit(app.exec())
