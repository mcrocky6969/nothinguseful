import json, os
from dataclasses import dataclass, field, asdict

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(ROOT, "config.json")
TPL_DIR = os.path.join(ROOT, "templates")
MOB_DIR = os.path.join(TPL_DIR, "mobs")
WZ_DIR = os.path.join(TPL_DIR, "wz")          # imported wz sprites: templates/wz/<mob>/<anim>_<n>.png
LIB_DIR = os.path.join(TPL_DIR, "library")      # ALL game mobs (import_mobs.py): templates/library/<slug>/ + library.json
MAPDB_PATH = os.path.join(ROOT, "mapdb.json")
os.makedirs(MOB_DIR, exist_ok=True)
os.makedirs(WZ_DIR, exist_ok=True)
os.makedirs(LIB_DIR, exist_ok=True)


@dataclass
class Config:
    window_title: str = "MapleStory"
    current_map: str = ""             # 9-digit map id from mapdb.json; only this map's library mobs are searched
    name_threshold: float = 0.80
    name_offset_y: int = -45          # name tag centre -> character centre (tune with Measure tool)
    attack_reach: int = 85            # basic attack, px in front of character
    attack_height: int = 53
    skill_enabled: bool = False
    skill_reach: int = 290
    det_extra_w: int = 365            # detection box = attack/skill reach + extra, each side
    det_extra_h: int = 67
    keys: dict = field(default_factory=lambda: {
        "left": "left", "right": "right", "attack": "ctrl",
        "skill": "shift", "hp": "pageup", "mp": "pagedown", "jump": "alt", "up": "up", "down": "down"})
    hp_pct: int = 50                  # press HP key below this %
    mp_pct: int = 30
    hp_roi: list = None               # [x1,y1,x2,y2] in game-frame pixels
    mp_roi: list = None
    part_match: bool = True           # also match left/right halves of sprites (mobs partly hidden behind loot)
    mm_roi: list = None               # minimap picture box [x1,y1,x2,y2] in game-frame pixels
    hunt: dict = field(default_factory=dict)       # map id -> [x1,y1,x2,y2] hunting zone in WORLD coordinates
    walk_speed: int = 125             # px/s at your speed stat
    jump_h: int = 65                  # tallest step/platform the character can jump up
    jump_dx: int = 110                # widest gap it can jump across
    mob_flip: bool = True             # also match mirrored sprites (left/right facing)
    sprite_scale: float = 1.0         # scale imported wz sprites if your client renders at another size
    track_ttl: float = 0.4            # keep a mob 'alive' this long after its last detection (stops flicker)
    monsters: dict = field(default_factory=dict)   # name -> {"on": bool, "thr": float}

    def save(self):
        with open(CFG_PATH, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @staticmethod
    def load():
        cfg = Config()
        if os.path.exists(CFG_PATH):
            data = json.load(open(CFG_PATH))
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        cfg.keys = {**Config().keys, **cfg.keys}
        return cfg
