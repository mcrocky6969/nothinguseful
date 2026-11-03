"""Platforms, ladders/ropes, link graph and routing built from one mapdb.json entry (world coordinates, y grows downward)."""
import bisect, heapq
from itertools import permutations
import cv2, numpy as np

STEP = 10                                   # node spacing along a platform (px)


class Platform:
    def __init__(self, pid, segs):
        self.id, self.segs = pid, sorted(segs)
        self.x0, self.x1 = self.segs[0][0], max(s[2] for s in self.segs)
        self.nodes, self.xs = [], []

    def y_at(self, x):
        x = min(max(x, self.x0), self.x1)
        for a, ya, b, yb in self.segs:
            if a <= x <= b:
                return ya + (yb - ya) * (x - a) / (b - a)
        s = min(self.segs, key=lambda g: min(abs(g[0] - x), abs(g[2] - x)))
        return s[1] if abs(s[0] - x) < abs(s[2] - x) else s[3]


class NavMap:
    def __init__(self, m, walk=125.0, jump_h=65, jump_dx=110):
        self.m, self.walk, self.JH, self.JDX = m, walk, jump_h, jump_dx
        self.plats, self.walls = self._platforms(m["footholds"])
        self.ladders = [dict(l, idx=i) for i, l in enumerate(m["ladders"])]
        self.nx, self.npid, self.adj = [], [], []
        self._nodes(); self._edges()
        xs = [s[0] for p in self.plats for s in p.segs] + [s[2] for p in self.plats for s in p.segs] or [0]
        ys = [s[1] for p in self.plats for s in p.segs] + [s[3] for p in self.plats for s in p.segs] + [l["y1"] for l in self.ladders] or [0]
        self.bounds = (min(xs) - 60, min(ys) - 80, max(xs) + 60, max(ys) + 80)

    # ---------------------------------------------------------------- geometry
    @staticmethod
    def _platforms(fh):
        seg, walls = {}, []
        for fid, x1, y1, x2, y2, prev, nxt in fh:
            if x1 == x2:
                walls.append((x1, min(y1, y2), max(y1, y2))); continue
            if x1 > x2:
                x1, y1, x2, y2 = x2, y2, x1, y1
            seg[fid] = (x1, y1, x2, y2, prev, nxt)
        parent = {k: k for k in seg}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]; a = parent[a]
            return a
        for k, v in seg.items():
            for o in (v[4], v[5]):
                if o in seg:
                    parent[find(k)] = find(o)
        ends = {}                                                      # footholds that touch end-to-end belong together
        for k, v in seg.items():
            for pt in ((v[0], v[1]), (v[2], v[3])):
                ends.setdefault(pt, []).append(k)
        for ks in ends.values():
            for o in ks[1:]:
                parent[find(ks[0])] = find(o)
        groups = {}
        for k, v in seg.items():
            groups.setdefault(find(k), []).append(v[:4])
        order = sorted(groups.values(), key=lambda g: (sum(s[1] + s[3] for s in g) / (2 * len(g)), min(s[0] for s in g)))
        return [Platform(i, g) for i, g in enumerate(order)], walls

    def _nodes(self):
        for p in self.plats:
            xs = list(range(p.x0, p.x1, STEP)) + [p.x1]
            p.xs = xs
            for x in xs:
                p.nodes.append(len(self.nx)); self.nx.append(x); self.npid.append(p.id)
        self.adj = [[] for _ in self.nx]

    def near(self, p, x):
        i = bisect.bisect_left(p.xs, x)
        i = min(max(i, 0), len(p.xs) - 1)
        if i > 0 and abs(p.xs[i - 1] - x) < abs(p.xs[i] - x):
            i -= 1
        return p.nodes[i]

    def _add(self, a, b, cost, act):
        self.adj[a].append((b, cost, act))

    def _below(self, p, x, ymin):
        best = None
        for q in self.plats:
            if q is not p and q.x0 <= x <= q.x1:
                y = q.y_at(x)
                if y > ymin and (best is None or y < best[0]):
                    best = (y, q)
        return best[1] if best else None

    def _edges(self):
        W, P_ = self.walk, self.plats
        for p in P_:
            for i, n in enumerate(p.nodes):
                x = p.xs[i]; y = p.y_at(x)
                if i:
                    d = abs(x - p.xs[i - 1]); self._add(n, p.nodes[i - 1], d / W, ("walk",)); self._add(p.nodes[i - 1], n, d / W, ("walk",))
                q = self._below(p, x, y + 6)
                if q:
                    self._add(n, self.near(q, x), 0.8 + (q.y_at(x) - y) / 500, ("down", x))
                if i in (0, len(p.nodes) - 1):                      # walking off the end
                    d = -1 if i == 0 else 1; x2 = x + 14 * d
                    q = self._below(p, x2, y - 4)
                    if q is None:
                        for qq in P_:
                            if qq is not p and qq.x0 <= x2 <= qq.x1 and qq.y_at(x2) > y - 4:
                                q = qq; break
                    if q:
                        self._add(n, self.near(q, x2), 0.6 + abs(q.y_at(x2) - y) / 500, ("walkoff", d))
                for q in P_:
                    if q is p:
                        continue
                    xq = min(max(x, q.x0), q.x1); dx = abs(xq - x); dy = q.y_at(xq) - y
                    if dx <= self.JDX and -self.JH <= dy <= 30 and not (dx == 0 and dy > 6):
                        self._add(n, self.near(q, xq), 1.0 + 3.0 * (dx / self.JDX) ** 2, ("jump", xq))
        for L in self.ladders:
            att = []
            for p in P_:
                if p.x0 - 25 <= L["x"] <= p.x1 + 25:
                    xc = min(max(L["x"], p.x0), p.x1); y = p.y_at(xc)
                    if L["y1"] - 25 <= y <= L["y2"] + 25:
                        att.append((p, xc, y))
            for (p, xa, ya), (q, xb, yb) in permutations(att, 2):
                if abs(ya - yb) > 10:
                    self._add(self.near(p, xa), self.near(q, xb), 0.6 + abs(ya - yb) / 90, ("climb", L["idx"], L["x"], yb))

    # ---------------------------------------------------------------- queries
    def locate(self, x, y, tol=40):
        best = None
        for p in self.plats:
            if p.x0 - 18 <= x <= p.x1 + 18:                         # the minimap dot is only +-1 px (16 world px) accurate sideways
                d = abs(p.y_at(x) - y)
                if d <= tol and (best is None or d < best[0]):
                    best = (d, p)
        return best[1] if best else None

    def ladder_at(self, x, y):
        for L in self.ladders:
            if abs(L["x"] - x) <= 18 and L["y1"] - 20 <= y <= L["y2"] + 20:
                return L
        return None

    def zone_nodes(self, zone):
        x1, y1, x2, y2 = zone
        out = [n for n, (x, pid) in enumerate(zip(self.nx, self.npid)) if x1 <= x <= x2 and y1 <= self.plats[pid].y_at(x) <= y2]
        if not out:                                                    # box drawn slightly off the platform: nearest bucket
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            best = min(range(len(self.nx)), key=lambda n: abs(self.nx[n] - cx) + 2 * abs(self.plats[self.npid[n]].y_at(self.nx[n]) - cy))
            out = [best]
        return set(out)

    def route(self, start, goals, pen=None):
        pen = pen or {}
        dist, prev, pq = {start: 0.0}, {}, [(0.0, start)]
        end = None
        while pq:
            d, u = heapq.heappop(pq)
            if u in goals:
                end = u; break
            if d > dist.get(u, 1e18):
                continue
            for v, c, act in self.adj[u]:
                c += pen.get((u, v), 0.0)
                if d + c < dist.get(v, 1e18):
                    dist[v] = d + c; prev[v] = (u, act); heapq.heappush(pq, (d + c, v))
        if end is None:
            return None
        steps = []
        while end in prev:
            u, act = prev[end]; steps.append((u, end, act)); end = u
        steps.reverse()
        legs = []                                                       # merge consecutive walks
        for u, v, act in steps:
            if act[0] == "walk" and legs and legs[-1]["kind"] == "walk" and legs[-1]["pid"] == self.npid[u]:
                legs[-1]["x_to"] = self.nx[v]
            else:
                legs.append({"kind": act[0], "act": act, "pid": self.npid[u], "x_from": self.nx[u], "x_to": self.nx[v], "pid_to": self.npid[v], "edge": (u, v)})
        return legs

    # ---------------------------------------------------------------- drawing (cv2 image, same look as the exploration map)
    def render(self, w, h, dot=None, zone=None, legs=None, cur_pid=None):
        x0, y0, x1, y1 = self.bounds
        s = min(w / (x1 - x0), h / (y1 - y0))
        ox, oy = (w - (x1 - x0) * s) / 2 - x0 * s, (h - (y1 - y0) * s) / 2 - y0 * s
        P = lambda x, y: (int(x * s + ox), int(y * s + oy))
        img = np.full((h, w, 3), (30, 30, 30), np.uint8)
        for L in self.ladders:
            cv2.line(img, P(L["x"], L["y1"]), P(L["x"], L["y2"]), (60, 60, 230) if not L["ladder"] else (40, 100, 240), 2)
        for p in self.plats:
            col = (0, 255, 255) if p.id == cur_pid else (60, 220, 60)
            for a, ya, b, yb in p.segs:
                cv2.line(img, P(a, ya), P(b, yb), col, 2)
            cv2.putText(img, f"P{p.id}", P(p.x0, p.y_at(p.x0) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
        for x, ya, yb in self.walls:
            cv2.line(img, P(x, ya), P(x, yb), (120, 120, 120), 1)
        if legs:
            for lg in legs:
                pa, pb = self.plats[lg["pid"]], self.plats[lg["pid_to"]]
                cv2.arrowedLine(img, P(lg["x_from"], pa.y_at(lg["x_from"]) - 4), P(lg["x_to"], pb.y_at(lg["x_to"]) - 4), (255, 160, 0), 2, tipLength=0.15)
        if zone:
            cv2.rectangle(img, P(zone[0], zone[1]), P(zone[2], zone[3]), (0, 220, 255), 2)
        if dot:
            cv2.circle(img, P(*dot), 6, (255, 255, 0), 2)
        return img, (s, ox, oy)
