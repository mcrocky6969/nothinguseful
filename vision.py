import glob, json, os, threading, time
from concurrent.futures import ThreadPoolExecutor
import cv2, numpy as np
from config import TPL_DIR, MOB_DIR, WZ_DIR, LIB_DIR, MAPDB_PATH


def bar_fill(frame, roi, kind):
    """Fraction (0-1) of the HP/MP bar that is filled, read from colour. roi = [x1,y1,x2,y2]."""
    if not roi:
        return None
    x1, y1, x2, y2 = roi
    c = frame[y1:y2, x1:x2]
    if c.size == 0:
        return None
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    if kind == "hp":
        m = ((h <= 10) | (h >= 170)) & (s > 110) & (v > 100)
    else:
        m = (h >= 95) & (h <= 130) & (s > 110) & (v > 100)
    idx = np.where(m.mean(axis=0) > 0.4)[0]
    return 0.0 if len(idx) == 0 else (idx.max() + 1) / c.shape[1]


def minimap_world(frame, roi, mm):
    """Find the player's (yellow) dot inside the minimap box and convert it to WORLD coordinates.
    The box must cover the minimap PICTURE only. Returns (world_x, world_y, dot_frame_x, dot_frame_y) or None."""
    x1, y1, x2, y2 = roi
    c = frame[y1:y2, x1:x2]
    if c.size == 0:
        return None
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    m = (hsv[..., 0] >= 18) & (hsv[..., 0] <= 36) & (hsv[..., 1] > 130) & (hsv[..., 2] > 190)
    ys, xs = np.where(m)
    if len(xs) < 2:
        return None
    dx, dy = float(xs.mean()) + 0.5, float(ys.mean()) + 0.5
    mag = 2 ** mm["mag"]
    scale = (x2 - x1) / (mm["width"] / mag)              # displayed px per minimap-canvas px (1.0 when the box is exact)
    return (dx / scale * mag - mm["centerX"], dy / scale * mag - mm["centerY"], x1 + dx, y1 + dy)


def _clean(r):
    return np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)


class Analyzer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.name_tpl = None
        self.mob_tpls, self.mob_counts = {}, {}
        self.tracks = []
        self._mapdb = None
        self.pool = ThreadPoolExecutor(max_workers=min(6, os.cpu_count() or 2))
        self._scan_in, self._scan_out, self._scan_t, self._scan_used = None, [], 0.0, 0.0
        self.scan_ms = 0.0
        self.load_templates()
        threading.Thread(target=self._scan_loop, daemon=True).start()

    # ---------------------------------------------------------------- map data
    def mapdb(self):
        if self._mapdb is None:
            try:
                self._mapdb = json.load(open(MAPDB_PATH, encoding="utf-8"))
            except Exception:
                self._mapdb = {}
        return self._mapdb

    def active_ids(self):
        m = self.mapdb().get(self.cfg.current_map)
        return {int(x["id"]) for x in m["mobs"]} if m else set()

    # ---------------------------------------------------------------- templates
    def load_templates(self):
        cfg = self.cfg
        p = os.path.join(TPL_DIR, "name.png")
        self.name_tpl = cv2.imread(p, cv2.IMREAD_GRAYSCALE) if os.path.exists(p) else None
        tpls, counts, masked = {}, {}, set()

        def add(name, gray, mask, anim, part=None):
            """part = (ox, oy, full_w, full_h): this template is a slice of a bigger sprite (see load_dir)."""
            sc = cfg.sprite_scale
            if mask is not None and sc != 1.0:
                gray = cv2.resize(gray, None, fx=sc, fy=sc, interpolation=cv2.INTER_NEAREST)
                mask = cv2.resize(mask, None, fx=sc, fy=sc, interpolation=cv2.INTER_NEAREST)
                part = None if part is None else tuple(int(v * sc) for v in part)
            variants = [(gray, mask, part)]
            if cfg.mob_flip:
                variants.append((cv2.flip(gray, 1), None if mask is None else cv2.flip(mask, 1),
                                 None if part is None else (-part[0], part[1], part[2], part[3])))
            for g, m, pt in variants:
                e = {"img": g, "mask": m, "anim": anim}
                if pt:
                    e.update(part=True, ox=pt[0], oy=pt[1], fw=pt[2], fh=pt[3])
                if m is not None and min(g.shape) >= 12:       # half-size copy for the fast coarse search
                    e["img_s"] = cv2.resize(g, None, fx=.5, fy=.5, interpolation=cv2.INTER_AREA)
                    e["mask_s"] = (cv2.resize(m, None, fx=.5, fy=.5, interpolation=cv2.INTER_AREA) > 127).astype(np.uint8) * 255
                tpls.setdefault(name, []).append(e)
            if part is None:
                counts[name] = counts.get(name, 0) + 1

        for f in sorted(glob.glob(os.path.join(MOB_DIR, "*.png"))):          # screenshot templates
            img = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                add(os.path.basename(f)[:-4].rsplit("_", 1)[0], img, None, "shot")
        def load_dir(d, name):
            for f in sorted(glob.glob(os.path.join(d, "*.png"))):
                img = cv2.imread(f, cv2.IMREAD_UNCHANGED)
                if img is None or img.ndim < 3 or img.shape[2] < 4:
                    continue
                mask = ((img[..., 3] > 128) * 255).astype(np.uint8)
                ys, xs = np.where(mask)
                if len(ys) < 20:
                    continue
                y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1      # crop to visible pixels
                gray = cv2.cvtColor(img[y0:y1, x0:x1, :3], cv2.COLOR_BGR2GRAY)
                mk = mask[y0:y1, x0:x1]
                anim = os.path.basename(f).split("_")[0].lower()
                add(name, gray, mk, anim)
                masked.add(name)
                w = gray.shape[1]
                if cfg.part_match and not anim.startswith("die") and w >= 24:     # left/right 60%: still matches when loot covers the rest
                    k = int(w * 0.6)
                    for s0 in (0, w - k):
                        if (mk[:, s0:s0 + k] > 0).sum() >= 60:
                            add(name, gray[:, s0:s0 + k], mk[:, s0:s0 + k], anim, part=(int(w / 2 - (s0 + k / 2)), 0, w, gray.shape[0]))

        for d in sorted(glob.glob(os.path.join(WZ_DIR, "*"))):               # hand-imported sprites (always active)
            load_dir(d, os.path.basename(d))
        idx_path, active = os.path.join(LIB_DIR, "library.json"), self.active_ids()
        if active and os.path.exists(idx_path):                              # library: only mobs spawning on the current map
            for slug, info in json.load(open(idx_path, encoding="utf-8")).items():
                if int(info["id"]) in active:
                    load_dir(os.path.join(LIB_DIR, slug), slug)
        for n in tpls:
            self.cfg.monsters.setdefault(n, {"on": True, "thr": 0.80 if n in masked else 0.70})
        self.mob_tpls, self.mob_counts, self.tracks = tpls, counts, []

    # ---------------------------------------------------------------- matching
    def _match_masked(self, sub, sub_s, e, thr):
        g, m = e["img"], e["mask"]
        h, w = g.shape
        if h > sub.shape[0] or w > sub.shape[1]:
            return []
        if "img_s" in e and e["img_s"].shape[0] <= sub_s.shape[0] and e["img_s"].shape[1] <= sub_s.shape[1]:
            r = _clean(cv2.matchTemplate(sub_s, e["img_s"], cv2.TM_CCOEFF_NORMED, mask=e["mask_s"]))
            ys, xs = np.where(r >= thr - 0.15)
            scale = 2
        else:
            r = _clean(cv2.matchTemplate(sub, g, cv2.TM_CCOEFF_NORMED, mask=m))
            ys, xs = np.where(r >= thr - 0.05)
            scale = 1
        if len(ys) > 40:
            k = np.argsort(r[ys, xs])[-40:]
            ys, xs = ys[k], xs[k]
        out = []
        for y, x in zip(ys * scale, xs * scale):          # refine each candidate at full resolution
            x0, y0 = max(0, x - 4), max(0, y - 4)
            patch = sub[y0:min(sub.shape[0], y + h + 4), x0:min(sub.shape[1], x + w + 4)]
            if patch.shape[0] < h or patch.shape[1] < w:
                continue
            rr = _clean(cv2.matchTemplate(patch, g, cv2.TM_CCOEFF_NORMED, mask=m))
            _, mx, _, loc = cv2.minMaxLoc(rr)
            if mx >= thr:
                out.append((float(mx), x0 + loc[0], y0 + loc[1]))
        return out

    def _job(self, args):
        sub, sub_s, name, e, thr = args
        if e.get("part"):
            thr += 0.07                                  # slices are less distinctive -> stricter
        g = e["img"]
        h, w = g.shape
        if e["mask"] is None:
            if h > sub.shape[0] or w > sub.shape[1]:
                return []
            res = cv2.matchTemplate(sub, g, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= thr)
            if len(ys) > 200:
                k = np.argsort(res[ys, xs])[-200:]
                ys, xs = ys[k], xs[k]
            raw = [(float(res[y, x]), x, y) for y, x in zip(ys, xs)]
        else:
            raw = self._match_masked(sub, sub_s, e, thr)
        return [(sc, x + w // 2 + e.get("ox", 0), y + h // 2 + e.get("oy", 0), name, e.get("fw", w), e.get("fh", h), e["anim"]) for sc, x, y in raw]

    def _find_mobs(self, gray, region, only=None):
        x0, y0, x1, y1 = region
        sub = gray[y0:y1, x0:x1]
        if sub.size == 0:
            return []
        sub_s = cv2.resize(sub, None, fx=.5, fy=.5, interpolation=cv2.INTER_AREA)
        jobs = []
        for name, entries in self.mob_tpls.items():
            m = self.cfg.monsters.get(name, {})
            if not m.get("on", True) or (only and name != only):
                continue
            jobs += [(sub, sub_s, name, e, m.get("thr", 0.70)) for e in entries]
        results = self.pool.map(self._job, jobs) if len(jobs) > 3 else map(self._job, jobs)
        hits = [(sc, x0 + cx, y0 + cy, name, w, h, anim) for r in results for sc, cx, cy, name, w, h, anim in r]
        hits.sort(reverse=True)
        kept = []
        for sc, cx, cy, name, w, h, anim in hits:          # non-max suppression scaled to sprite size
            if all(abs(cx - k[0]) >= max(8, 0.4 * w) or abs(cy - k[1]) >= max(8, 0.4 * h) for k in kept):
                kept.append((cx, cy, name, sc, w, h, anim))
        return kept

    def _scan_loop(self):
        """Background full-region search that discovers NEW mobs; known mobs are re-matched locally every frame."""
        last = None
        while True:
            job = self._scan_in
            if job is None or job is last:
                time.sleep(0.02)
                continue
            last = job
            gray, region = job
            t = time.time()
            try:
                self._scan_out = self._find_mobs(gray, region)
            except Exception:
                self._scan_out = []
            self.scan_ms = (time.time() - t) * 1000
            self._scan_t = time.time()
            time.sleep(0.03)

    # ---------------------------------------------------------------- tracking
    def _track(self, dets, now):
        for cx, cy, name, sc, w, h, anim in dets:
            if anim.startswith("die"):                      # a dying sprite: drop the live track immediately
                for t in self.tracks:
                    if t["name"] == name and abs(t["x"] - cx) < w and abs(t["y"] - cy) < h:
                        t["seen"] = 0
                continue
            best, bd = None, 70
            for t in self.tracks:
                d = abs(t["x"] - cx) + abs(t["y"] - cy)
                if t["name"] == name and d < bd:
                    best, bd = t, d
            if best:
                best.update(x=0.4 * best["x"] + 0.6 * cx, y=0.4 * best["y"] + 0.6 * cy, sc=sc, w=w, h=h, seen=now)
            else:
                self.tracks.append({"x": cx, "y": cy, "name": name, "sc": sc, "w": w, "h": h, "seen": now})
        self.tracks = [t for t in self.tracks if now - t["seen"] <= self.cfg.track_ttl]
        return [dict(t, age=now - t["seen"], fresh=now - t["seen"] < 0.1) for t in self.tracks]

    def analyse(self, frame):
        cfg, now = self.cfg, time.time()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        H, W = gray.shape
        out = {"player": None, "name_score": 0.0, "mobs": [], "hp": None, "mp": None}
        if self.name_tpl is not None and self.name_tpl.shape[0] < H and self.name_tpl.shape[1] < W:
            res = cv2.matchTemplate(gray, self.name_tpl, cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            out["name_score"] = float(mx)
            if mx >= cfg.name_threshold:
                h, w = self.name_tpl.shape[:2]
                out["player"] = (loc[0] + w // 2, loc[1] + h // 2 + cfg.name_offset_y)
        region = (0, 0, W, H)
        if out["player"]:       # only search inside the detection box -> much faster
            px, py = out["player"]
            rw = (max(cfg.attack_reach, cfg.skill_reach) if cfg.skill_enabled else cfg.attack_reach) + cfg.det_extra_w
            rh = cfg.attack_height // 2 + cfg.det_extra_h
            region = (max(0, px - rw), max(0, py - rh), min(W, px + rw), min(H, py + rh))
        self._scan_in = (gray, region)                       # hand the frame to the background scanner
        dets = []
        if self._scan_t > self._scan_used:                   # a fresh full scan finished -> use it once
            dets += self._scan_out
            self._scan_used = self._scan_t
        for t in list(self.tracks):                          # cheap local re-match around each known mob
            x, y = int(t["x"]), int(t["y"])
            win = (max(0, x - t["w"] - 30), max(0, y - t["h"] - 20), min(W, x + t["w"] + 30), min(H, y + t["h"] + 20))
            dets += self._find_mobs(gray, win, only=t["name"])
        out["mobs"] = self._track(dets, now)
        out["mm"] = None
        mm = (self.mapdb().get(cfg.current_map) or {}).get("minimap")
        if cfg.mm_roi and mm and mm.get("mag") is not None:
            out["mm"] = minimap_world(frame, cfg.mm_roi, mm)
        out["hp"] = bar_fill(frame, cfg.hp_roi, "hp")
        out["mp"] = bar_fill(frame, cfg.mp_roi, "mp")
        return out


class VisionWorker(threading.Thread):
    """Grabs + analyses frames off the GUI thread. Everyone reads .latest = (frame, result, time)."""

    def __init__(self, cap, analyzer, hz=25):
        super().__init__(daemon=True)
        self.cap, self.an, self.hz = cap, analyzer, hz
        self.latest, self.fps, self.running = None, 0.0, True

    def run(self):
        n, t0 = 0, time.time()
        while self.running:
            s = time.time()
            frame = self.cap.grab()
            if frame is None:
                self.latest = None
                time.sleep(0.5)
                continue
            self.latest = (frame, self.an.analyse(frame), time.time())
            n += 1
            if time.time() - t0 >= 1:
                self.fps, n, t0 = n / (time.time() - t0), 0, time.time()
            time.sleep(max(0.0, 1 / self.hz - (time.time() - s)))
