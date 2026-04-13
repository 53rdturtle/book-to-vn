extends Control

const BundleLoaderScript = preload("res://scripts/BundleLoader.gd")

@onready var background: TextureRect = $Background
@onready var char_left: TextureRect = $CharLeft
@onready var char_middle: TextureRect = $CharMiddle
@onready var char_right: TextureRect = $CharRight
@onready var bgm_player: AudioStreamPlayer = $BGMPlayer
@onready var se_player: AudioStreamPlayer = $SEPlayer
@onready var voice_player: AudioStreamPlayer = $VoicePlayer
@onready var speaker_label: Label = $DialogueBox/SpeakerName
@onready var dialogue_label: RichTextLabel = $DialogueBox/DialogueText

var loader: BundleLoaderScript
var cast: Dictionary = {}
var commands: Array = []
var idx: int = 0
var chapter_idx: int = 0
var chapter_count: int = 0
var char_slots: Dictionary = {}

const TYPE_CHARS_PER_SEC: float = 45.0
var is_typing: bool = false
var type_accum: float = 0.0
var total_chars: int = 0

func _ready() -> void:
    char_slots = {
        "left": char_left,
        "middle": char_middle,
        "right": char_right,
    }
    for node in char_slots.values():
        node.texture = null
        node.set_meta("char_id", "")
    dialogue_label.visible_characters_behavior = TextServer.VC_CHARS_AFTER_SHAPING

    var bundle_path := _parse_bundle_arg()
    if bundle_path == "":
        speaker_label.text = ""
        dialogue_label.text = "[No --bundle <path> argument provided]"
        return

    loader = BundleLoaderScript.new(bundle_path)
    cast = loader.load_cast()
    chapter_count = loader.chapter_files().size()
    _load_chapter(0)

func _parse_bundle_arg() -> String:
    var args: PackedStringArray = OS.get_cmdline_user_args()
    for i in args.size():
        if args[i] == "--bundle" and i + 1 < args.size():
            return args[i + 1]
    return ""

func _input(event: InputEvent) -> void:
    var advance := false
    if event is InputEventKey and event.pressed and not event.echo:
        if event.keycode == KEY_SPACE or event.keycode == KEY_ENTER:
            advance = true
    elif event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
        advance = true
    if advance:
        if is_typing:
            _finish_typing()
        else:
            _advance_until_say()

func _process(delta: float) -> void:
    if not is_typing:
        return
    type_accum += delta * TYPE_CHARS_PER_SEC
    var shown := int(type_accum)
    if shown >= total_chars:
        _finish_typing()
    else:
        dialogue_label.visible_characters = shown

func _finish_typing() -> void:
    is_typing = false
    dialogue_label.visible_characters = -1

func _load_chapter(ch_idx: int) -> void:
    chapter_idx = ch_idx
    idx = 0
    var chapter: Dictionary = loader.load_chapter(chapter_idx)
    commands = chapter.get("commands", [])
    for node in char_slots.values():
        node.texture = null
        node.set_meta("char_id", "")
    _advance_until_say()

func _advance_until_say() -> void:
    while idx < commands.size():
        var cmd: Dictionary = commands[idx]
        idx += 1
        if _apply(cmd):
            return
    if chapter_idx + 1 < chapter_count:
        _load_chapter(chapter_idx + 1)
    else:
        speaker_label.text = ""
        dialogue_label.text = "[End]"

func _apply(cmd: Dictionary) -> bool:
    var t: String = cmd.get("type", "")
    match t:
        "bg":
            _set_texture(background, loader.bg_path(cmd["id"]))
        "char_show":
            var slot: String = cmd["slot"]
            if char_slots.has(slot):
                var node: TextureRect = char_slots[slot]
                _set_texture(node, loader.char_path(cmd["id"], cmd["expr"]))
                node.set_meta("char_id", cmd["id"])
        "char_hide":
            var target_id: String = cmd["id"]
            for slot in char_slots.keys():
                var node: TextureRect = char_slots[slot]
                if node.get_meta("char_id", "") == target_id:
                    node.texture = null
                    node.set_meta("char_id", "")
        "bgm_play":
            var stream := _load_ogg(loader.bgm_path(cmd["id"]))
            if stream:
                bgm_player.stream = stream
                bgm_player.play()
        "bgm_stop":
            bgm_player.stop()
        "se":
            var stream2 := _load_ogg(loader.se_path(cmd["id"]))
            if stream2:
                se_player.stream = stream2
                se_player.play()
        "say":
            speaker_label.text = _display_name(String(cmd.get("speaker", "")))
            dialogue_label.text = String(cmd.get("text", ""))
            total_chars = dialogue_label.get_total_character_count()
            dialogue_label.visible_characters = 0
            type_accum = 0.0
            is_typing = total_chars > 0
            var vs := _load_ogg(loader.voice_path(String(cmd.get("voice_clip", ""))))
            if vs:
                voice_player.stream = vs
                voice_player.play()
            return true
    return false

func _set_texture(rect: TextureRect, path: String) -> void:
    if not FileAccess.file_exists(path):
        push_warning("Missing image: " + path)
        return
    var img := Image.new()
    var err := img.load(path)
    if err != OK:
        push_warning("Failed to load image: " + path)
        return
    rect.texture = ImageTexture.create_from_image(img)

func _display_name(speaker: String) -> String:
    if speaker == "" or speaker == "narrator":
        return ""
    var entry: Dictionary = cast.get(speaker, {})
    return String(entry.get("display_name", speaker))

func _load_ogg(path: String) -> AudioStreamOggVorbis:
    if path == "" or not FileAccess.file_exists(path):
        return null
    return AudioStreamOggVorbis.load_from_file(path)
