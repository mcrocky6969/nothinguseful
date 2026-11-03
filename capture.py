import threading
import cv2, mss, numpy as np, win32con, win32gui


class Capture:
    """Grabs the client area of the game window (window must be visible, not covered)."""

    def __init__(self, title):
        self.title = title
        self.hwnd = None
        self.reason = ""
        self._tl = threading.local()

    def find(self):
        found = []

        def cb(h, _):
            t = win32gui.GetWindowText(h)
            if win32gui.IsWindowVisible(h) and self.title.lower() in t.lower() and "pixel" not in t.lower():
                found.append(h)

        win32gui.EnumWindows(cb, None)
        self.hwnd = found[0] if found else None
        return self.hwnd

    def _covered_by(self):
        """None if the game's client area is visible, else the title of the window covering it."""
        l, t, r, b = win32gui.GetClientRect(self.hwnd)
        pts = [(.5, .5), (.1, .1), (.9, .1), (.1, .9), (.9, .9)]
        bad, who = 0, ""
        for i, (fx, fy) in enumerate(pts):
            h = win32gui.WindowFromPoint(win32gui.ClientToScreen(self.hwnd, (int(r * fx), int(b * fy))))
            root = win32gui.GetAncestor(h, win32con.GA_ROOT) if h else 0
            if root != self.hwnd:
                if i == 0:
                    bad += 2
                bad += 1
                who = who or (win32gui.GetWindowText(root) or "another window")
        return who if bad >= 2 else None

    def grab(self):
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            if not self.find():
                self.reason = "game window not found"
                return None
        if win32gui.IsIconic(self.hwnd):
            self.reason = "game minimised"
            return None
        who = self._covered_by()
        if who:                                   # never read pixels that belong to another window (Firefox, Studio ...)
            self.reason = f"game covered by '{who}'"
            return None
        self.reason = ""
        l, t, r, b = win32gui.GetClientRect(self.hwnd)
        x, y = win32gui.ClientToScreen(self.hwnd, (0, 0))
        if r - l < 50 or b - t < 50:
            return None
        sct = getattr(self._tl, "sct", None)
        if sct is None:
            sct = self._tl.sct = mss.mss()
        img = np.array(sct.grab({"left": x, "top": y, "width": r - l, "height": b - t}))
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
