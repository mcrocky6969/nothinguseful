import random, threading, time
import pydirectinput, win32gui

pydirectinput.PAUSE = 0


class Bot:
    def __init__(self, cfg, worker, cap, nav, log):
        self.cfg, self.worker, self.cap, self.nav, self.log = cfg, worker, cap, nav, log
        self.state = "stopped"          # stopped | running | paused
        self.status = "stopped"
        self.thread = None
        self.held = set()
        self.facing, self.vx = 1, 0.0
        self.next_atk, self.last_jump, self.last_t = 0, 0, time.time()
        self.patrol_dir, self.patrol_mode, self.patrol_until = 1, "walk", 0
        self.low = {"hp": 0, "mp": 0}
        self.last_pot = {"hp": 0, "mp": 0}
        self.last_warn, self.lock_key, self.lock_n = {}, None, 0

    # ---- control ----
    def start(self):
        self.state = "running"
        if not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def pause(self):
        if self.state == "running":
            self.state = "paused"

    def stop(self):
        self.state = "stopped"

    # ---- input ----
    def _warn(self, key, msg, every=3):
        if time.time() - self.last_warn.get(key, 0) > every:
            self.last_warn[key] = time.time()
            self.log(msg)

    def _tap(self, key, lo=0.03, hi=0.07):
        pydirectinput.keyDown(key)
        time.sleep(random.uniform(lo, hi))
        pydirectinput.keyUp(key)

    def _hold_keys(self, keys):
        """Hold exactly this set of keys down (smooth walking / climbing)."""
        for k in self.held - keys:
            pydirectinput.keyUp(k)
        for k in keys - self.held:
            pydirectinput.keyDown(k)
        self.held = set(keys)

    def _hold(self, key):
        self._hold_keys({key} if key else set())
        self.vx = 0.0

    def _dir_key(self, side):
        return self.cfg.keys["right" if side > 0 else "left"]

    def _apply(self, it, now):
        """it = {"move": -1|0|1, "up", "down", "jump"} from the navigator."""
        k, c = set(), self.cfg
        d = it.get("move", 0)
        if d:
            k.add(self._dir_key(d)); self.facing = d
        if it.get("up"):
            k.add(c.keys["up"])
        if it.get("down"):
            k.add(c.keys["down"])
        self._hold_keys(k)
        self.vx = d * c.walk_speed if (d and not it.get("up") and not it.get("down")) else 0.0
        if it.get("jump") and now - self.last_jump > 0.5:
            self._tap(c.keys["jump"], 0.04, 0.08); self.last_jump = now

    # ---- behaviours ----
    def _vitals(self, a, now):
        c = self.cfg
        for kind, pct in (("hp", c.hp_pct), ("mp", c.mp_pct)):
            v = a[kind]
            if v is None:
                continue
            if v < 0.02:
                self._warn(kind + "0", f"{kind.upper()} bar reads 0% - redo the {kind.upper()} region in Studio", 10)
                self.low[kind] = 0
                continue
            self.low[kind] = self.low[kind] + 1 if v * 100 < pct else 0
            if self.low[kind] >= 3 and now - self.last_pot[kind] > 1.0:
                self._tap(c.keys[kind])
                self.last_pot[kind], self.low[kind] = now, 0
                self.log(f"{kind.upper()} {v*100:.0f}% < {pct}% -> potion")

    def _free_patrol(self, now):
        if now >= self.patrol_until:
            if self.patrol_mode == "walk" and random.random() < 0.3:
                self.patrol_mode, self.patrol_until = "idle", now + random.uniform(0.3, 0.9)
            else:
                self.patrol_mode = "walk"; self.patrol_dir *= -1
                self.patrol_until = now + random.uniform(2.0, 5.0)
        if self.patrol_mode == "walk":
            self._apply({"move": self.patrol_dir}, now)
        else:
            self._hold(None)

    def _step(self, a, now):
        c, nav = self.cfg, self.nav
        dt = min(0.2, now - self.last_t); self.last_t = now
        self._vitals(a, now)
        nav.update(a, self.vx, dt, now)
        p = a["player"]
        if not p:
            self._hold(None)
            self.status = "name tag LOST"
            self._warn("name", "name tag not found (redo Name Tracking in Studio)")
            return
        px, py = p
        reach = max(c.attack_reach, c.skill_reach) if c.skill_enabled else c.attack_reach
        det_w = reach + c.det_extra_w
        target = None
        if not nav.in_transit():                                          # never stop to fight while jumping / on a rope
            same = [m for m in a["mobs"] if abs(m["x"] - px) <= det_w and abs(m["y"] - py) <= c.attack_height
                    and nav.zone_ok(nav.world_x_of(m["x"] - px))]
            if same:
                target = min(same, key=lambda m: abs(m["x"] - px))
        if target:
            dx = target["x"] - px
            side = 1 if dx >= 0 else -1
            key = (target["name"], int(target["x"]) // 60)
            if key != self.lock_key:
                self.lock_key, self.lock_n = key, self.lock_n + 1
                self.log(f"LOCK #{self.lock_n} {target['name']} at screen ({target['x']:.0f},{target['y']:.0f}) gap {abs(dx):.0f}px")
            in_basic = abs(dx) <= c.attack_reach * 0.95
            in_skill = c.skill_enabled and abs(dx) <= c.skill_reach * 0.95
            if in_basic or in_skill:
                self._hold(None)
                self.status = f"attacking {target['name']} ({'left' if side < 0 else 'right'}, gap {abs(dx):.0f}px)"
                if side != self.facing:
                    self._tap(self._dir_key(side), 0.02, 0.04); self.facing = side
                if now >= self.next_atk and target["age"] <= 0.25:         # never swing at a remembered/dead mob
                    self._tap(c.keys["attack"] if in_basic else c.keys["skill"])
                    self.next_atk = now + random.uniform(0.22, 0.38)
            elif nav.ready() and nav.blocked(side):
                self._hold(None)
                self.status = f"{target['name']} out of reach (platform edge)"
            else:
                self.status = f"walking to {target['name']} (gap {abs(dx):.0f}px)"
                self._apply({"move": side}, now)
            return
        if nav.ready():
            it = nav.intent(now)
            self.status = nav.status
            self._apply(it or {"move": 0}, now)
        else:
            if c.current_map:
                self.status = "no minimap position -> free patrol (set the Minimap box in Studio)"
            else:
                self.status = "no map selected -> free patrol"
            self._warn("nonav", self.status, 15)
            self._free_patrol(now)

    def _run(self):
        self.log("bot started")
        last_focus = 0
        while self.state != "stopped":
            if self.state == "paused":
                self._hold(None); self.status = "paused"; time.sleep(0.2); continue
            lt = self.worker.latest
            now = time.time()
            if lt is None or now - lt[2] > 0.6:
                self._hold(None)
                self.status = "waiting: " + (self.cap.reason or "no fresh frames")
                self._warn("game", "paused - " + (self.cap.reason or "no fresh frames") + " (keys released)")
                time.sleep(0.2); continue
            if now - last_focus > 2:
                try: win32gui.SetForegroundWindow(self.cap.hwnd)
                except Exception: pass
                last_focus = now
            try:
                self._step(lt[1], now)
            except Exception as e:
                self.log(f"error: {e}")
            time.sleep(0.04)
        self._hold(None)
        self.status = "stopped"
        self.log("bot stopped")
