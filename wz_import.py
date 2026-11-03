"""Import PNG sprites exported from wzpy ("PNG bundle") into templates/wz/<name>/.
The animation name is read from the folder/file name (stand / move / fly / hit1 / die1 ...).
die* frames mark a monster as dying so the bot stops hitting it."""
import hashlib, os, re, shutil
import cv2
from config import WZ_DIR

_ANIM = re.compile(r"^(stand|move|walk|fly|hit\d*|die\d*)$")


def detect_anim(path):
    for tok in reversed(re.split(r"[\\/._\- ]+", path.lower())):
        if _ANIM.match(tok):
            return "move" if tok == "walk" else tok
    return None


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def import_paths(items, name, root=WZ_DIR):
    """items = [(png_path, anim)] -> copies to templates/wz/<name>/<anim>_<n>.png (RGBA only, duplicates skipped)."""
    name = re.sub(r"[^A-Za-z0-9]", "", name) or "mob"
    dst = os.path.join(root, name)
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    seen, counts, no_alpha = set(), {}, 0
    for p, anim in sorted(items, key=lambda t: _natural(t[0])):
        img = cv2.imread(p, cv2.IMREAD_UNCHANGED)
        if img is None or img.ndim < 3 or img.shape[2] < 4:
            no_alpha += 1
            continue
        key = (anim, hashlib.md5(open(p, "rb").read()).hexdigest())
        if key in seen:
            continue
        seen.add(key)
        counts[anim] = counts.get(anim, 0) + 1
        shutil.copy(p, os.path.join(dst, f"{anim}_{counts[anim]}.png"))
    return name, counts, no_alpha


def import_sprites(src, name):
    """One monster's exported folder."""
    base = os.path.basename(os.path.normpath(src))
    items = []
    for root, _, files in os.walk(src):
        for f in files:
            if f.lower().endswith(".png"):
                p = os.path.join(root, f)
                a = detect_anim(os.path.join(base, os.path.relpath(p, src)))
                if a:
                    items.append((p, a))
    return import_paths(items, name)
