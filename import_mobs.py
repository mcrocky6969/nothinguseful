"""Import ALL monsters from a wzpy PNG export of Mob/_Canvas_*.wz into templates/library/, named from String Mob.img.json.
Run once (and again after a game patch). You never pick a map here: in the Studio you choose your current map and the
bot only searches for the monsters that spawn there (from mapdb.json).

  python import_mobs.py --png-root "C:/export/_Canvas_000" --strings "C:/.../Mob.img.json" [--png-root ... for more files]

The mob id is read from the path (e.g. 0000002.img) and the animation (stand/move/fly/hit1/die1) from folder/file names."""
import argparse, json, os, re, shutil
from collections import defaultdict
from config import LIB_DIR
from wz_import import detect_anim, import_paths

ap = argparse.ArgumentParser()
ap.add_argument("--png-root", required=True, nargs="+")
ap.add_argument("--strings", required=True)
ap.add_argument("--keep", action="store_true", help="keep the existing library (add to it)")
a = ap.parse_args()

names = {}
for c in json.load(open(a.strings, encoding="utf-8"))["children"]:
    n = next((x.get("value") for x in c.get("children", []) if x["name"] == "name"), None)
    if n:
        names[int(c["name"])] = n

found = defaultdict(list)                      # mob id -> [(png, anim)]
for root_dir in a.png_root:
    for root, _, files in os.walk(root_dir):
        for f in files:
            if f.lower().endswith(".png"):
                p = os.path.join(root, f)
                m = re.search(r"(?<!\d)(\d{7})\.img", p) or re.search(r"(?<!\d)(\d{7})(?!\d)", p)
                anim = detect_anim(p[m.end():]) if m else None
                if anim:
                    found[int(m.group(1))].append((p, anim))

idx_path = os.path.join(LIB_DIR, "library.json")
index = {}
if a.keep and os.path.exists(idx_path):
    index = json.load(open(idx_path, encoding="utf-8"))
elif not a.keep:
    shutil.rmtree(LIB_DIR, ignore_errors=True); os.makedirs(LIB_DIR)
used = set(index)
for mid, items in sorted(found.items()):
    slug = re.sub(r"[^A-Za-z0-9]", "", names.get(mid, f"mob{mid}")).lower() or f"mob{mid}"
    if slug in used and index.get(slug, {}).get("id") != mid:
        slug += str(mid)
    used.add(slug)
    slug, counts, no_alpha = import_paths(items, slug, root=LIB_DIR)
    index[slug] = {"id": mid, "name": names.get(mid, "?")}
    print(f"{mid:07d} {names.get(mid, '?'):28s} {counts}" + (f"  ({no_alpha} without alpha skipped)" if no_alpha else ""))
json.dump(index, open(idx_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(f"library: {len(index)} monsters")
