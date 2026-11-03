"""Keeps track of WHERE the character is (minimap dot + dead reckoning) and decides how to get to / stay inside the hunt zone.
intent(now) -> {"move": -1|0|1, "up": bool, "down": bool, "jump": bool} for the bot to press, or None when unknown."""
import random
from nav import NavMap

CLIMB_OFFSETS = (0, 8, -8, 16, -16)          # rope grab attempts: minimap is only +-16px accurate, so we probe around the rope


class Navigator:
    def __init__(self, cfg, analyzer, log):
        self.cfg, self.an, self.log = cfg, analyzer, log
        self.nm, self._key, self.map_id = None, None, ""
        self.x = self.y = None
        self.cur = None                      # platform the character stands on (or None: air / rope)
        self.legs, self.leg = [], None
        self.fails, self.status = 0, "no map selected"
        self.mm_ok, self.dot = False, None
        self.pt_x, self.pause_until, self.wander_until, self.wander_dir = None, 0, 0, 1
        self._last_pid, self._last_log = None, {}
        self.pen = {}
        self.phase, self.attempt, self.stepped, self.y0 = 0, 0, False, 0

    # ---------------------------------------------------------------- map / position
    def sync(self):
        c = self.cfg
        key = (c.current_map, c.walk_speed, c.jump_h, c.jump_dx)
        if key == self._key:
            return
        self._key, self.map_id = key, c.current_map
        m = self.an.mapdb().get(c.current_map)
        self.nm = NavMap(m, c.walk_speed, c.jump_h, c.jump_dx) if m else None
        self.x = self.y = self.cur = None
        self.legs, self.leg, self.pen = [], None, {}
        self.status = "map loaded" if m else "no map selected"

    def zone(self):
        z = self.cfg.hunt.get(self.map_id)
        return tuple(z) if z else None

    def update(self, a, vx, dt, now):
        """vx = current walking velocity in px/s (dead reckoning between minimap readings)."""
        self.sync()
        if self.nm is None:
            return
        if self.x is not None:
            self.x += vx * dt
        mm = a.get("mm")
        self.mm_ok, self.dot = mm is not None, (mm[0], mm[1]) if mm else None
        if mm:
            wx, wy = mm[0], mm[1]
            if self.x is None or abs(wx - self.x) > 48:
                self.x, self.y = wx, wy
            else:
                self.x, self.y = 0.75 * self.x + 0.25 * wx, 0.6 * self.y + 0.4 * wy
        if self.x is None:
            return
        self.cur = self.nm.locate(self.x, self.y)
        if self.leg and self.leg["kind"] == "climb" and self.phase >= 1 and not self.stepped:
            self.cur = None                                   # minimap is too coarse to tell 'on rope near the top' from 'on the platform'
        pid = self.cur.id if self.cur else None
        if pid != self._last_pid and pid is not None:
            self._note("plat", f"standing on platform P{pid} (x={self.x:.0f})", 0.5)
        self._last_pid = pid

    def ready(self):
        return self.nm is not None and self.x is not None

    def _note(self, key, msg, gap=1.0):
        import time
        if time.time() - self._last_log.get(key, 0) >= gap:
            self._last_log[key] = time.time(); self.log(msg)

    # ---------------------------------------------------------------- helpers for the bot
    def in_transit(self):
        return self.leg is not None and self.leg["kind"] != "walk"

    def blocked(self, d):
        """True when walking in direction d would step off the current platform."""
        p = self.cur
        return p is not None and ((d > 0 and self.x >= p.x1 - 6) or (d < 0 and self.x <= p.x0 + 6))

    def world_x_of(self, screen_dx):
        return None if self.x is None else self.x + screen_dx

    def zone_ok(self, wx, margin=50):
        z = self.zone()
        return True if (z is None or wx is None) else z[0] - margin <= wx <= z[2] + margin

    def in_zone(self):
        z = self.zone()
        if z is None or self.cur is None:
            return z is None
        return z[0] <= self.x <= z[2] and z[1] <= self.cur.y_at(self.x) <= z[3]

    # ---------------------------------------------------------------- decisions
    def intent(self, now):
        if not self.ready():
            return None
        if now < self.wander_until:
            return {"move": self.wander_dir}
        if self.leg:
            r = self._run_leg(now)
            if r is not None:
                return r
        if self.cur is None:                                   # in the air or on a rope with no active leg
            L = self.nm.ladder_at(self.x, self.y)
            if L:
                z = self.zone()
                up = z is not None and (z[1] + z[3]) / 2 < self.y
                self.status = "on rope"
                return {"up": up, "down": not up}
            return {"move": 0}
        if self.zone() and not self.in_zone():
            return self._plan(now)
        self.status = "in hunting zone" if self.zone() else "no hunt zone: patrolling this platform"
        return self._patrol(now)

    def _plan(self, now):
        nm, zone = self.nm, self.zone()
        start = nm.near(self.cur, self.x)
        legs = nm.route(start, nm.zone_nodes(zone), self.pen)
        if legs is None:
            self.status = "no route to hunting zone"
            self._note("noroute", f"no route from P{self.cur.id} to the hunting zone (check jump height / map)", 5)
            return self._patrol(now)
        self.legs = legs
        self._note("route", f"outside the hunting zone -> route P{self.cur.id} -> P{legs[-1]['pid_to']}: " +
                   ", ".join(l["kind"] + (f" P{l['pid']}->P{l['pid_to']}" if l["kind"] != "walk" else "") for l in legs if l["kind"] != "walk") , 2)
        self._next(now)
        return self._run_leg(now) or {"move": 0}

    def _patrol(self, now):
        p, z = self.cur, self.zone()
        lo, hi = p.x0 + 10, p.x1 - 10
        if z:
            lo, hi = max(lo, z[0]), min(hi, z[2])
        if hi - lo < 30:
            return {"move": 0}
        if now < self.pause_until:
            return {"move": 0}
        if self.pt_x is None or abs(self.x - self.pt_x) < 8 or not (lo - 20 <= self.pt_x <= hi + 20):
            self.pt_x = hi if (self.pt_x is None or self.pt_x <= (lo + hi) / 2) else lo
            if random.random() < 0.4:
                self.pause_until = now + random.uniform(0.3, 0.9)
        return {"move": 1 if self.pt_x > self.x else -1}

    # ---------------------------------------------------------------- leg state machine
    def _next(self, now):
        self.leg = self.legs.pop(0) if self.legs else None
        self.leg_t = self.p_t = now
        self.phase = self.attempt = 0
        self.stepped = False
        if self.leg is None:
            self.fails = 0
            self._note("zone", "arrived at the hunting zone", 1)

    def _jump_dir(self, L, x):
        """Direction to hold while jumping: toward the target platform, or straight up when we are already underneath it."""
        tp = self.nm.plats[L["pid_to"]]
        d = L["x_to"] - L["x_from"]
        if abs(d) > 12:
            return 1 if d > 0 else -1
        if tp.x0 + 4 <= L["x_from"] <= tp.x1 - 4:                    # directly under/over it: no sideways drift
            return 0
        return 1 if (tp.x0 + tp.x1) / 2 > L["x_from"] else -1       # at the wall/edge: push toward the platform

    def _fail(self, why, now):
        self.fails += 1
        if self.leg and self.leg.get("edge"):                       # make the planner try something else next time
            self.pen[self.leg["edge"]] = self.pen.get(self.leg["edge"], 0) + 4.0
        self._note("fail", f"leg failed ({why}) -> replanning (fail #{self.fails})", 0.3)
        self.leg, self.legs = None, []
        if self.fails >= 3:
            self.fails = 0
            self.wander_until, self.wander_dir = now + random.uniform(0.6, 1.4), random.choice((-1, 1))
        return {"move": 0}

    def _run_leg(self, now):
        L, x, y, cur, nm = self.leg, self.x, self.y, self.cur, self.nm
        kind, pid, pid_to = L["kind"], L["pid"], L["pid_to"]
        if now - self.leg_t > {"walk": 25, "jump": 6, "down": 5, "walkoff": 5, "climb": 14}[kind]:
            return self._fail("timeout", now)
        sgn = lambda v: 1 if v > 0 else -1
        self.status = f"route: {kind} P{pid}->P{pid_to}"
        if kind == "walk":
            if cur is None or cur.id != pid:
                return self._fail("left platform", now)
            if abs(x - L["x_to"]) <= 7:
                self._next(now); return self._run_leg(now) if self.leg else None
            return {"move": sgn(L["x_to"] - x)}
        if cur is not None and cur.id == pid_to and (self.phase > 0 or kind == "walkoff"):      # landed
            self._next(now); return self._run_leg(now) if self.leg else {"move": 0}
        if kind == "walkoff":
            if cur is None or cur.id == pid:
                return {"move": L["act"][1]}
            return self._fail("wrong platform", now)
        if kind in ("jump", "down"):
            if self.phase == 0:
                if cur is None or cur.id != pid:
                    return self._fail("lost platform", now)
                if abs(x - L["x_from"]) > 7:
                    return {"move": sgn(L["x_from"] - x)}
                self.phase, self.p_t = 1, now
                return {"move": self._jump_dir(L, x) if kind == "jump" else 0, "jump": True, "down": kind == "down"}
            if now - self.p_t > 1.8:
                return self._fail("did not land", now)
            return {"move": self._jump_dir(L, x) if kind == "jump" else 0}
        if kind == "climb":                                            # probe around the rope x until we start moving vertically
            rope_x, yb = L["act"][2], L["act"][3]
            up = yb < (self.y0 if self.phase else y)
            if self.phase == 0:
                tx = rope_x + CLIMB_OFFSETS[self.attempt % len(CLIMB_OFFSETS)]
                if cur is not None and cur.id == pid:
                    if abs(x - tx) > 5:
                        return {"move": sgn(tx - x)}
                elif nm.ladder_at(x, y) is None and cur is None:
                    return self._fail("lost rope", now)
                self.phase, self.p_t, self.y0 = 1, now, y
                up = yb < y
            if self.phase == 1:
                if abs(y - self.y0) >= 12 or (cur is None and nm.ladder_at(x, y)):
                    self.phase, self.p_t = 2, now
                elif now - self.p_t > 0.7:
                    self.attempt += 1
                    if self.attempt >= len(CLIMB_OFFSETS):
                        return self._fail("could not grab rope", now)
                    self.phase = 0
                    return {"move": 0}
                return {"up": up, "down": not up}
            if self.phase == 2:
                if up and y <= yb + 18:                                 # at the top: hop onto the platform
                    tp = nm.plats[pid_to]
                    d = sgn((tp.x0 + tp.x1) / 2 - x)
                    if not self.stepped:
                        self.stepped, self.step_t = True, now
                    elif now - self.step_t > 1.5:                       # hop did not work: back onto the rope and retry
                        self.stepped = False
                    return {"move": d, "jump": True, "up": now - self.step_t > 0.6}
                if not up and y >= yb - 18:
                    self.stepped = True                                 # bottom reached: platform detection may resume
                if now - self.p_t > 9:
                    return self._fail("climb timeout", now)
                return {"up": up, "down": not up}
        return {"move": 0}
