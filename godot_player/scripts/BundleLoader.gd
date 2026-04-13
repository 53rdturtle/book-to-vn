extends RefCounted

var root: String
var book: Dictionary

func _init(path: String) -> void:
    root = path.rstrip("/\\")
    var txt := FileAccess.get_file_as_string(root + "/book.json")
    if txt == "":
        push_error("Failed to read book.json at " + root)
        book = {}
        return
    var parsed: Variant = JSON.parse_string(txt)
    book = parsed if parsed is Dictionary else {}

func chapter_files() -> Array:
    var out: Array = []
    for ch in book.get("chapters", []):
        out.append(root + "/" + String(ch["file"]))
    return out

func load_chapter(idx: int) -> Dictionary:
    var files := chapter_files()
    if idx < 0 or idx >= files.size():
        return {}
    var txt := FileAccess.get_file_as_string(files[idx])
    var parsed: Variant = JSON.parse_string(txt)
    return parsed if parsed is Dictionary else {}

func bg_path(id: String) -> String:
    return "%s/assets/bg/%s.png" % [root, id]

func char_path(id: String, expr: String) -> String:
    return "%s/assets/char/%s/%s.png" % [root, id, expr]

func bgm_path(id: String) -> String:
    return "%s/assets/bgm/%s.ogg" % [root, id]

func se_path(id: String) -> String:
    return "%s/assets/se/%s.ogg" % [root, id]

func voice_path(clip: String) -> String:
    return "%s/voice/%s" % [root, clip]

func load_cast() -> Dictionary:
    var txt := FileAccess.get_file_as_string(root + "/cast.json")
    if txt == "":
        return {}
    var parsed: Variant = JSON.parse_string(txt)
    if not (parsed is Dictionary):
        return {}
    return parsed.get("cast", {})
