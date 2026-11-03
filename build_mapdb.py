"""Builds mapdb.json (footholds, ladders, portals, mob spawns, minimap scale, names) from your wzpy JSON export.

  python build_mapdb.py --maps "C:/Users/you/Downloads/Map.json_bundle/Map/Map" --strings "C:/path/Map.img.json"
  (optional) --only 40000 100000000     only build these map ids (quick test)
"""
import argparse, glob, json, os, re


def kids(n):
    return {c["name"]: c for c in n.get("children", [])}


def props(n):
    return {c["name"]: c.get("value") for c in n.get("children", []) if "value" in c}


def parse_map(path):
    root = kids(json.load(open(path, encoding="utf-8")))
    info = props(root.get("info", {}))
    fhs = []
    for gid, grp in kids(root.get("foothold", {})).items():
        for lid, layer in kids(grp).items():
            for fid, fh in kids(layer).items():
                p = props(fh)
                if "x1" in p:
                    fhs.append([int(fid), p["x1"], p["y1"], p["x2"], p["y2"], p.get("prev", 0), p.get("next", 0)])
    ladders = []
    for lid, l in kids(root.get("ladderRope", {})).items():
        p = props(l)
        ladders.append({"x": p["x"], "y1": p["y1"], "y2": p["y2"], "ladder": bool(p.get("l", 0)), "uf": p.get("uf", 0)})
    mobs = []
    for lid, l in kids(root.get("life", {})).items():
        p = props(l)
        if p.get("type") == "m":
            mobs.append({"id": p["id"], "x": p["x"], "y": p.get("cy", p["y"]), "fh": p.get("fh"), "rx0": p.get("rx0"), "rx1": p.get("rx1")})
    portals = []
    for lid, l in kids(root.get("portal", {})).items():
        p = props(l)
        portals.append({"name": p.get("pn"), "type": p.get("pt"), "x": p.get("x"), "y": p.get("y"), "to": p.get("tm"), "target": p.get("tn")})
    mm = props(root.get("miniMap", {}))
    return {"vr": [info.get("VRLeft"), info.get("VRTop"), info.get("VRRight"), info.get("VRBottom")],
            "town": info.get("town", 0), "minimap": {k: mm.get(k) for k in ("width", "height", "centerX", "centerY", "mag")} if mm else None,
            "footholds": fhs, "ladders": ladders, "mobs": mobs, "portals": portals}


def load_names(path):
    out = {}
    if not path:
        return out
    for cat in json.load(open(path, encoding="utf-8"))["children"]:
        for m in cat.get("children", []):
            p = props(m)
            out[m["name"]] = {"street": p.get("streetName", ""), "name": p.get("mapName", "")}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", required=True)
    ap.add_argument("--strings")
    ap.add_argument("--out", default="mapdb.json")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    names = load_names(a.strings)
    db = {}
    for f in glob.glob(os.path.join(a.maps, "Map*", "*.img.json")):
        match = re.match(r"(\d+)", os.path.basename(f))
        if not match:
            continue
        mid = match.group(1).zfill(9)
        if a.only and mid not in a.only:
            continue
        try:
            m = parse_map(f)
        except Exception as e:
            print("skip", f, e); continue
        if not m["footholds"]:
            continue
        m.update(names.get(mid, {"street": "", "name": ""}))
        db[mid] = m
    json.dump(db, open(a.out, "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)
    print(f"wrote {a.out}: {len(db)} maps")