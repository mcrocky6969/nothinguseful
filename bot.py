import random, threading, time
import pydirectinput, win32gui

pydirectinput.PAUSE = 0

STATES = ["IDLE", "TRAVEL", "PATROL", "FIND", "COMBAT", "HEAL", "LOOT"]
# IDLE    stopped / paused / game hidden / name tag lost       HEAL   drinking a potion (HP/MP below your %)
# TRAVEL  following a route (walk, jump, climb) back to the hunting zone     LOOT   pressing the pickup key after a kill
# PATROL  inside the zone, walking it to look for monsters                  FIND   monster seen, walking up to it
# COMBAT  monster in attack range: face it and attack


class Bot:
    def __init__(self, cfg, worker, cap, nav, log):
        self.cfg, self.worker, self.cap, self.nav, self.log = cfg, worker, cap, nav, log
        self.state = "stopped"                      # run switch: stopped | running | paused
        self.fsm, self.fsm_why, self.fsm_t, self._fsm_log_t = "IDLE", "stopped", time.time(), 0.0
        self.status = "stopped"
        self.thread = None
        self.held = set()
        self.facing, self.vx = 1, 0.0
        self.next_atk, self.last_jump, self.last_t = 0, 0, time.time()
        self.heal_until = self.loot_until = self.next_loot = self.engaged_t = 0.0
        self.combat_key, self.combat_since, self.ignored = None, 0.0, {}
        self.patrol_dir, self.patrol_mode, self.patrol_until = 1, "walk", 0
        self.low = {"hp": 0, "mp": 0}
        self.last_pot = {"hp": 0, "mp": 0}
        self.last_warn, self.lock_key, self.lock_n = {}, None, 0

    # ---- run switch ----
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

    # ---- FSM bookkeeping ----
    def _set(self, st, why=""):
        self.fsm_why = why
        self.status = f"{st}: {why}" if why else st
        if st != self.fsm:
            now = time.time()
            if now - self._fsm_log_t > 0.3 or st in ("COMBAT", "HEAL", "LOOT", "IDLE"):
                self.log(f"FSM {self.fsm} -> {st}" + (f"  ({why})" if why else ""))
                self._fsm_log_t = now
            self.fsm, self.fsm_t = st, now

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
        """it = {"move": -1|0|1, "up", "down", "jump"}."""
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

    # ---- HEAL ----
    def _vitals(self, a, now):
        """True when a potion was just pressed."""
        c, pressed = self.cfg, False
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
                self.last_pot[kind], self.low[kind], pressed = now, 0, True
                self.log(f"{kind.upper()} {v*100:.0f}% < {pct}% -> potion")
        return pressed

    # ---- PATROL without a map ----
    def _free_patrol(self, now):
        if now >= self.patrol_until:
            if self.patrol_mode == "walk" and random.random() < 0.3:
                self.patrol_mode, self.patrol_until = "idle", now + random.uniform(0.3, 0.9)
            else:
                self.patrol_mode = "walk"; self.patrol_dir *= -1
                self.patrol_until = now + random.uniform(2.0, 5.0)
        self._apply({"move": self.patrol_dir if self.patrol_mode == "walk" else 0}, now)

    def _check_stuck(self, it, now):
        moving = bool(it.get("move")) and not it.get("up") and not it.get("down")
        if self.nav.stuck(moving, now):
            d = -(it.get("move") or 1)
            self.log("not moving (wall / edge / bad position?) -> turning around")
            self.nav.wander_until, self.nav.wander_dir, self.nav.pt_x = now + random.uniform(0.8, 1.4), d, None
            self.patrol_dir, self.patrol_until = d, now + 1.0

    # ---- one tick of the state machine ----
    def _step(self, a, now):
        c, nav = self.cfg, self.nav
        dt = min(0.2, now - self.last_t); self.last_t = now
        nav.update(a, self.vx, dt, now)
        p = a["player"]
        if not p:                                                         # -> IDLE
            self._hold(None)
            self._set("IDLE", "name tag LOST")
            self._warn("name", "name tag not found (redo Name Tracking in Studio)")
            return
        if self._vitals(a, now):                                          # -> HEAL (interrupts everything)
            self.heal_until = now + 0.45
        if now < self.heal_until:
            self._hold(None); self._set("HEAL", "drinking potion")
            return
        if now < self.loot_until:                                         # -> LOOT
            self._hold(None); self._set("LOOT", "picking up drops")
            if now >= self.next_loot:
                self._tap(c.keys["loot"]); self.next_loot = now + 0.25
            return

        px, py = p
        reach = max(c.attack_reach, c.skill_reach) if c.skill_enabled else c.attack_reach
        det_w = reach + c.det_extra_w
        target = None
        if not nav.in_transit():                                          # never stop to fight while jumping / on a rope
            same = [m for m in a["mobs"] if abs(m["x"] - px) <= det_w and abs(m["y"] - py) <= c.attack_height
                    and self.ignored.get((m["name"], int(m["x"]) // 60), 0) < now
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
            if in_basic or in_skill:                                      # -> COMBAT
                self._hold(None)
                self._set("COMBAT", f"{target['name']} {'left' if side < 0 else 'right'}, gap {abs(dx):.0f}px")
                self.engaged_t = now
                if key != self.combat_key:
                    self.combat_key, self.combat_since = key, now
                elif now - self.combat_since > 8:                         # attacking something that never dies = false detection
                    self.ignored[key] = now + 8
                    self.log(f"{target['name']} is not dying after 8s -> ignoring it for 8s (false detection?)")
                if side != self.facing:
                    self._tap(self._dir_key(side), 0.02, 0.04); self.facing = side
                if now >= self.next_atk and target["age"] <= 0.25:         # never swing at a remembered/dead mob
                    self._tap(c.keys["attack"] if in_basic else c.keys["skill"])
                    self.next_atk = now + random.uniform(0.22, 0.38)
            elif nav.ready() and nav.blocked(side):                       # -> FIND (but the platform ends before it)
                self._hold(None)
                self._set("FIND", f"{target['name']} is past the platform edge - waiting")
            else:                                                         # -> FIND
                self._set("FIND", f"walking to {target['name']} (gap {abs(dx):.0f}px)")
                self._apply({"move": side}, now)
            return

        self.combat_key = None
        if c.loot_on and self.engaged_t and now - self.engaged_t < 1.5:   # we were fighting and the target is gone -> LOOT
            self.engaged_t = 0
            self.loot_until, self.next_loot = now + c.loot_time, now
            self._hold(None); self._set("LOOT", "target gone")
            return
        self.engaged_t = 0
        if nav.ready():
            it = nav.intent(now) or {"move": 0}
            away = nav.leg is not None or bool(nav.legs) or (nav.zone() is not None and not nav.in_zone())
            self._set("TRAVEL" if away else "PATROL", nav.status)         # -> TRAVEL / PATROL
            self._check_stuck(it, now)
            self._apply(it, now)
        else:
            why = "no map selected" if not c.current_map else "no minimap position (Minimap box / dot?)"
            self._set("PATROL", why + " - free patrol")
            self._warn("nonav", why + " -> free patrol (walks blindly, may fall off platforms)", 15)
            self._free_patrol(now)

    def _run(self):
        self.log("bot started")
        last_focus = 0
        while self.state != "stopped":
            if self.state == "paused":
                self._hold(None); self._set("IDLE", "paused"); time.sleep(0.2); continue
            lt = self.worker.latest
            now = time.time()
            if lt is None or now - lt[2] > 0.6:
                self._hold(None)
                self._set("IDLE", self.cap.reason or "no fresh frames")
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
        self._set("IDLE", "stopped")
        self.log("bot stopped")
