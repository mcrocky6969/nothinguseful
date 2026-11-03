"""Everything that turns the game's MINIMAP PICTURE into world coordinates.

  find_dot()      the player marker = a bright, saturated yellow disc (~6x6 px) with a grey ring
  align()         the picture is a rendering of the map, so the platform lines from mapdb must sit on its grass/terrain edges;
                  we search the (dx, dy) offset that makes them match -> your drawn box does not have to be pixel-exact
  render_explore  the zoomed "exploration map": minimap picture (blocky pixels) + platforms/ropes/route/zone drawn on top
Coordinates: world -> canvas px = (world + center) / 2**mag ; crop px = canvas px + offset."""
import cv2, numpy as np

DOT_DY = 3.75          # the dot is drawn about this many minimap px ABOVE the platform the character stands on


def find_dot(crop):
    """-> (x, y) centre of the player dot in crop pixels, or None."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= 20) & (hsv[..., 0] <= 34) & (hsv[..., 1] >= 170) & (hsv[..., 2] >= 235)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    best = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < 10 or w > 12 or h > 12 or abs(int(w) - int(h)) > 3:        # speckles of foliage are 1-3 px; the dot is a ~6x6 disc
            continue
        if best is None or a > best[0]:
            best = (a, x + w / 2.0, y + h / 2.0)
    return None if best is None else (best[1], best[2])


_pts = {}


def _points(m):
    key = id(m)
    if key not in _pts:
        mm = m["minimap"]; mag = 2 ** mm["mag"]
        X, Y = [], []
        for _, x1, y1, x2, y2, _, _ in m["footholds"]:
            if x1 == x2:
                continue
            n = max(3, int(abs(x2 - x1) / mag) * 2)
            t = np.linspace(0, 1, n)
            X.append((x1 + (x2 - x1) * t + mm["centerX"]) / mag); Y.append((y1 + (y2 - y1) * t + mm["centerY"]) / mag)
        _pts[key] = (np.concatenate(X), np.concatenate(Y)) if X else (np.zeros(0), np.zeros(0))
    return _pts[key]


def _features(crop):
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    green = ((hsv[..., 0] >= 35) & (hsv[..., 0] <= 55) & (hsv[..., 1] > 90) & (hsv[..., 2] > 110)).astype(np.float32)
    green = cv2.dilate(green, np.ones((3, 1), np.uint8))                        # tolerate +-1 px vertically
    gy = np.abs(cv2.Sobel(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_32F, 0, 1, ksize=3))
    return green + 0.35 * (gy > 70).astype(np.float32)


def align(crop, m, search=36):
    """-> (ox, oy, confidence 0..1). Search the offset where the foothold lines lie on the picture's platform edges."""
    X, Y = _points(m)
    if len(X) == 0:
        return 0, 0, 0.0
    F = _features(crop)
    H, W = F.shape
    best, scores = (-1.0, 0, 0), []
    for oy in range(-search, search + 1):
        iy = np.rint(Y + oy).astype(int)
        vy = (iy >= 0) & (iy < H)
        for ox in range(-search, search + 1):
            ix = np.rint(X + ox).astype(int)
            v = vy & (ix >= 0) & (ix < W)
            s = float(F[iy[v], ix[v]].sum()) if v.any() else 0.0
            scores.append(s)
            if s > best[0]:
                best = (s, ox, oy)
    conf = best[0] / max(1.0, 1.3 * len(X))
    typical = float(np.median(scores)) / max(1.0, 1.3 * len(X))
    return best[1], best[2], max(0.0, conf - typical)                            # how much better than a random offset


def world_from_dot(dot, off, m, feet=True):
    """dot in crop px -> (world_x, world_y). feet=True moves from the dot centre down to the platform surface."""
    mm = m["minimap"]; mag = 2 ** mm["mag"]
    cx, cy = dot[0] - off[0], dot[1] - off[1] + (DOT_DY if feet else 0.0)
    return cx * mag - mm["centerX"], cy * mag - mm["centerY"]


# ------------------------------------------------------------------ exploration map (like the original bot's)
def render_explore(crop, m, nm, off, zoom=2, dot_world=None, est=None, zone=None, legs=None, cur_pid=None, aligned=True):
    """Zoomed minimap picture (blocky pixels) with platforms (green), ropes (red), hunting zone (yellow), route (orange)
    and you (cyan ring) drawn on it."""
    mm = m["minimap"]; mag = 2 ** mm["mag"]
    big = cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    P = lambda wx, wy: (int(((wx + mm["centerX"]) / mag + off[0]) * zoom), int(((wy + mm["centerY"]) / mag + off[1]) * zoom))
    for p in nm.plats:
        col = (0, 255, 255) if p.id == cur_pid else (0, 230, 0)
        for a, ya, b, yb in p.segs:
            cv2.line(big, P(a, ya), P(b, yb), col, 1)
    for L in nm.ladders:
        cv2.line(big, P(L["x"], L["y1"]), P(L["x"], L["y2"]), (40, 40, 255), 1)
    if zone:
        cv2.rectangle(big, P(zone[0], zone[1]), P(zone[2], zone[3]), (0, 220, 255), 1)
    for lg in (legs or []):
        pa, pb = nm.plats[lg["pid"]], nm.plats[lg["pid_to"]]
        cv2.arrowedLine(big, P(lg["x_from"], pa.y_at(lg["x_from"])), P(lg["x_to"], pb.y_at(lg["x_to"])), (0, 150, 255), 1, tipLength=0.3)
    if est:
        cv2.circle(big, P(est[0], est[1]), 3 * zoom, (255, 255, 0), 1)             # where the bot thinks you are
    # header with legend
    bar = np.full((18, big.shape[1], 3), (40, 40, 40), np.uint8)
    cv2.putText(bar, f"EXPLORATION MAP ({zoom}x)" + ("" if aligned else "  NOT ALIGNED"), (4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255) if aligned else (80, 80, 255), 1)
    x = 150 * zoom // 2 + 20
    for txt, col in (("you", (255, 255, 0)), ("walk", (0, 230, 0)), ("climb", (40, 40, 255)), ("hunt zone", (0, 220, 255))):
        cv2.circle(bar, (x, 9), 3, col, -1); cv2.putText(bar, txt, (x + 6, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (200, 200, 200), 1)
        x += 14 + 7 * len(txt)
    return np.vstack([bar, big])
