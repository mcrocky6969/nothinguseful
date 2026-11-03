import os
import json

CANVAS_JSON_DIR = r"C:\Users\ronni\Downloads\_canvas\wz_root" 
STRING_MOB_JSON = r"C:\Users\ronni\Downloads\string wz\wz_root\Mob.img.json" 
OUTPUT_FILE = "mobdb.json"

def load_json(filepath):
    if not os.path.exists(filepath):
        return {}
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def extract_all_strings(node, result_dict):
    """Recursively search for mob ID to name mappings in any nested structure."""
    if isinstance(node, dict):
        # Check if this dictionary represents a mob entry with a name
        if "name" in node and isinstance(node["name"], (str, dict)):
            name_val = node["name"]
            if isinstance(name_val, dict):
                name_val = name_val.get("value", "Unknown")
            # Look for an ID associated with it
            for k, v in node.items():
                if k.isdigit() and len(k) >= 3:
                    result_dict[str(int(k))] = str(name_val)
        
        for k, v in node.items():
            if k.isdigit() and isinstance(v, dict):
                # If the key itself is a mob ID
                name = v.get("name")
                if isinstance(name, dict):
                    name = name.get("value")
                if name:
                    result_dict[str(int(k))] = str(name)
            extract_all_strings(v, result_dict)
    elif isinstance(node, list):
        for item in node:
            extract_all_strings(item, result_dict)

def build_mob_database():
    print("Loading String.wz Mob data...")
    raw_string_data = load_json(STRING_MOB_JSON)
    
    names_by_id = {}
    extract_all_strings(raw_string_data, names_by_id)

    mob_database = {}
    
    print(f"Scanning Canvas JSON directory: {CANVAS_JSON_DIR}")
    if os.path.exists(CANVAS_JSON_DIR):
        canvas_files = [f for f in os.listdir(CANVAS_JSON_DIR) if f.endswith('.json')]
    else:
        canvas_files = []

    for file_name in canvas_files:
        raw_id = file_name.split('.')[0]
        try:
            norm_id = str(int(raw_id))
        except ValueError:
            continue
            
        # Get name from string data, or fallback to formatted ID
        mob_name = names_by_id.get(norm_id, f"Mob_{norm_id}")
        
        file_path = os.path.join(CANVAS_JSON_DIR, file_name)
        canvas_data = load_json(file_path)
        
        # Extract actions (like move, stand, hit1, die1) from canvas data structures
        actions = set()
        def find_actions(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    # Action folders in MapleStory WZ files
                    if k in ["stand", "move", "hit1", "die1", "die2", "attack1", "attack2", "fly", "jump"]:
                        actions.add(k)
                    elif isinstance(v, dict) and not k.isdigit() and k not in ["children", "img", "Canvas"]:
                        # Check if key looks like an action name
                        if any(act in k.lower() for act in ["stand", "move", "hit", "die", "attack", "fly"]):
                            actions.add(k)
                        find_actions(v)
            elif isinstance(node, list):
                for item in node:
                    find_actions(item)

        find_actions(canvas_data)
        
        # Fallback: if no named actions found via deep search, grab top-level non-metadata keys
        if not actions and isinstance(canvas_data, dict):
            ignore = {"name", "type", "children", "v", "offset", "info", "canvas", "format", "width", "height", "origin", "slot_total", "renderable"}
            for k in canvas_data.keys():
                if k.lower() not in ignore and not k.isdigit():
                    actions.add(k)
            if "children" in canvas_data and isinstance(canvas_data["children"], dict):
                for k in canvas_data["children"].keys():
                    if k.lower() not in ignore and not k.isdigit():
                        actions.add(k)

        mob_database[norm_id] = {
            "id": int(norm_id),
            "formatted_id": raw_id.zfill(7),
            "name": mob_name,
            "available_actions": sorted(list(actions))
        }

    # Save output
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(mob_database, f, indent=4, ensure_ascii=False)

    print(f"Successfully generated {OUTPUT_FILE} with {len(mob_database)} mobs!")

if __name__ == "__main__":
    build_mob_database()