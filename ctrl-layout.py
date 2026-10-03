#!/usr/bin/python3
"""Manage Ctrl-release layout selection in Ubuntu GNOME Wayland sessions."""

import base64
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from gi.repository import Gio, GLib

state_directory = (
    Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    / "ctrl-layout"
)
xkb_directory = (
    Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "xkb"
)
keyd_config_path = Path("/etc/keyd/ctrl-layout.conf")
test_state_directory = state_directory.parent / "ctrl-layout-conditional-test"
settings: Gio.Settings
state_file = state_directory / "state.json"
OPTION = "ctrl_layout:conditional"
SOURCES = [["xkb", "us"], ["xkb", "ru"]]
FILES = {
    "rules/evdev": """! include %S/evdev

! option = symbols
  ctrl_layout:conditional = +ctrl_layout(conditional)
""",
    "symbols/ctrl_layout": """partial function_keys
xkb_symbols "conditional" {
 replace key <FK13> {
  type[Group1] = "ONE_LEVEL", type[Group2] = "ONE_LEVEL",
  symbols[Group1] = [ VoidSymbol ], symbols[Group2] = [ ISO_Next_Group ],
  actions[Group1] = [ NoAction() ], actions[Group2] = [ NoAction() ]
 };
 replace key <FK14> {
  type[Group1] = "ONE_LEVEL", type[Group2] = "ONE_LEVEL",
  symbols[Group1] = [ ISO_Next_Group ], symbols[Group2] = [ VoidSymbol ],
  actions[Group1] = [ NoAction() ], actions[Group2] = [ NoAction() ]
 };
 replace key <MENU> { [ Menu ], actions[Group1] = [ NoAction() ] };
};
""",
}


def run_command(*args):
    subprocess.run(args, check=True)


def is_keyd_active():
    return (
        subprocess.run(
            ["systemctl", "is-active", "--quiet", "keyd"], check=False
        ).returncode
        == 0
    )


def find_keyd_binary():
    return shutil.which("keyd.rvaiya") or shutil.which("keyd")


def read_optional_text(path):
    return path.read_text() if path.exists() else None


def get_setting(key):
    return settings.get_value(key).unpack()


def set_setting(key, value):
    variant_type = "a(ss)" if key == "sources" else "as"
    if not settings.is_writable(key) or not settings.set_value(
        key, GLib.Variant(variant_type, value)
    ):
        raise RuntimeError("Не удалось записать " + key)
    Gio.Settings.sync()


def save_state(record):
    """Atomically save the installation record without changing its format."""
    state_directory.mkdir(parents=True, exist_ok=True)
    state_directory.chmod(0o700)
    temporary_path = state_directory / "state.tmp"
    temporary_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    temporary_path.replace(state_file)


def write_keyd_config(value):
    if value is None:
        if keyd_config_path.exists():
            run_command("sudo", "rm", "--", str(keyd_config_path))
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=state_directory
        ) as temporary_file:
            temporary_file.write(value)
            temporary_file.flush()
            run_command(
                "sudo",
                "install",
                "-m",
                "644",
                temporary_file.name,
                str(keyd_config_path),
            )


def write_xkb_files(values):
    for name, value in values.items():
        path = xkb_directory / name
        if value is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)


def check_managed_state(record):
    """Refuse to overwrite configuration changed outside this installer."""
    if read_optional_text(keyd_config_path) not in (
        None,
        record["config"],
        record["config_before"],
    ):
        raise RuntimeError("Конфиг keyd изменён вручную; перезапись отменена.")
    for name, value in FILES.items():
        if read_optional_text(xkb_directory / name) not in (
            value,
            record["files_before"][name],
        ):
            raise RuntimeError("Файл XKB изменён вручную: " + name)
    if list(get_setting("xkb-options")) not in (
        record["options"],
        record["options_before"],
    ):
        raise RuntimeError(
            "xkb-options изменены после установки; автоматическая перезапись отменена."
        )
    if [list(x) for x in get_setting("sources")] not in (
        SOURCES,
        record["sources_before"],
    ):
        raise RuntimeError(
            "Список раскладок изменён после установки; автоматическая перезапись отменена."
        )


def refresh_keyd(record, restore=False):
    others = [
        p for p in keyd_config_path.parent.glob("*.conf") if p != keyd_config_path
    ]
    if keyd_config_path.exists() or others:
        run_command("sudo", "systemctl", "start", "keyd")
        run_command("sudo", find_keyd_binary(), "reload")
    else:
        run_command("sudo", "systemctl", "stop", "keyd")
    # Preserve service use by other keyd configurations.
    if restore and not others:
        run_command(
            "sudo",
            "systemctl",
            "enable" if record["service_enabled"] else "disable",
            "keyd",
        )
        if not record["service_active"]:
            run_command("sudo", "systemctl", "stop", "keyd")


def validate_xkb(record):
    """Compile the keymap and verify conditional switching in both groups."""
    xkb = ctypes.CDLL("libxkbcommon.so.0")

    class XkbRuleNames(ctypes.Structure):
        _fields_ = [
            (k, ctypes.c_char_p)
            for k in ("rules", "model", "layout", "variant", "options")
        ]

    for name, args, result in [
        ("xkb_context_new", [ctypes.c_int], ctypes.c_void_p),
        (
            "xkb_keymap_new_from_names",
            [ctypes.c_void_p, ctypes.POINTER(XkbRuleNames), ctypes.c_int],
            ctypes.c_void_p,
        ),
        ("xkb_state_new", [ctypes.c_void_p], ctypes.c_void_p),
        (
            "xkb_state_update_mask",
            [ctypes.c_void_p] + [ctypes.c_uint] * 6,
            ctypes.c_uint,
        ),
        ("xkb_state_key_get_one_sym", [ctypes.c_void_p, ctypes.c_uint], ctypes.c_uint),
        (
            "xkb_state_update_key",
            [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int],
            ctypes.c_uint,
        ),
        ("xkb_state_serialize_layout", [ctypes.c_void_p, ctypes.c_uint], ctypes.c_uint),
    ]:
        function = getattr(xkb, name)
        function.argtypes = args
        function.restype = result
    context = xkb.xkb_context_new(0)
    names = XkbRuleNames(
        b"evdev", b"pc105", b"us,ru", b"", ",".join(record["options"]).encode()
    )
    keymap = xkb.xkb_keymap_new_from_names(context, ctypes.byref(names), 0)
    if not keymap:
        raise RuntimeError("Ошибка компиляции XKB")
    for group in (0, 1):
        for target, key in ((0, 191), (1, 192)):
            keyboard_state = xkb.xkb_state_new(keymap)
            xkb.xkb_state_update_mask(keyboard_state, 0, 0, 0, 0, 0, group)
            if xkb.xkb_state_key_get_one_sym(keyboard_state, key) != (
                0xFE08 if group != target else 0xFFFFFF
            ):
                raise RuntimeError("Проверка условного переключения не пройдена")
            xkb.xkb_state_update_key(keyboard_state, key, 1)
            xkb.xkb_state_update_key(keyboard_state, key, 0)
            if xkb.xkb_state_serialize_layout(keyboard_state, 128) != group:
                raise RuntimeError("Обнаружено прямое переключение XKB")


def parse_keyboard_ids(text):
    if text is None:
        return []
    match = re.fullmatch(
        r"\s*\[ids\]\s*\n(.*?)\n\[global\]\s*\noverload_tap_timeout\s*=\s*0\s*\n\[main\]\s*\nleftcontrol\s*=\s*overload\(control,\s*f13\)\s*\nrightcontrol\s*=\s*overload\(control,\s*f14\)\s*",
        text,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError(
            "Существующий конфиг не соответствует управляемому назначению Ctrl."
        )
    keyboard_ids = match[1].split()
    if not keyboard_ids or any(
        not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}(?::[0-9a-fA-F]+)?", v)
        for v in keyboard_ids
    ):
        raise RuntimeError("В конфиге ожидаются явные ID клавиатур.")
    return list(dict.fromkeys(v.lower() for v in keyboard_ids))


def build_keyd_config(keyboard_ids):
    return (
        "[ids]\n"
        + "\n".join(keyboard_ids)
        + "\n\n[global]\noverload_tap_timeout = 0\n\n[main]\nleftcontrol = overload(control, f13)\nrightcontrol = overload(control, f14)\n"
    )


def confirmation_hint():
    """Choose the hint from the current Xwayland keyboard group."""
    # Read the active XKB group mirrored by Mutter into Xwayland.
    # Do not infer the input language from locale or the (possibly locked) MRU.
    try:
        x11 = ctypes.CDLL("libX11.so.6")
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XkbGetState.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
        x11.XkbGetState.restype = ctypes.c_int
        x11.XkbKeycodeToKeysym.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ubyte,
            ctypes.c_int,
            ctypes.c_int,
        ]
        x11.XkbKeycodeToKeysym.restype = ctypes.c_ulong
        display = x11.XOpenDisplay(None)
        if not display:
            return "[y/N · д/Н]"
        try:
            # XkbStateRec begins with unsigned char group; reserve aligned storage
            # larger than the complete public structure to receive all fields.
            keyboard_state = (ctypes.c_ulong * 8)()
            if x11.XkbGetState(display, 0x100, ctypes.byref(keyboard_state)) != 0:
                return "[y/N · д/Н]"
            group = ctypes.cast(keyboard_state, ctypes.POINTER(ctypes.c_ubyte))[0]
            symbol = x11.XkbKeycodeToKeysym(display, 24, group, 0)
            if symbol == 0x71:
                return "[y/N]"  # q in the US layout
            if symbol in (0x6CA, 0x1000439):
                return "[д/Н]"  # й in RU
            return "[y/N · д/Н]"
        finally:
            x11.XCloseDisplay(display)
    except (OSError, AttributeError):
        return "[y/N · д/Н]"


def ask_add_keyboard():
    # Read interactive answers from the controlling terminal, independent of stdin.
    with open("/dev/tty", "r") as tty_in, open("/dev/tty", "w") as tty_out:
        while True:
            tty_out.write("Добавить ещё клавиатуру? " + confirmation_hint() + ": ")
            tty_out.flush()
            answer = tty_in.readline()
            if not answer:
                raise RuntimeError("Терминал закрыт; настройка отменена.")
            answer = answer.strip().lower()
            if answer in ("", "н", "нет", "n", "no"):
                return False
            if answer in ("д", "да", "y", "yes"):
                return True


def collect_keyboards(existing):
    """Collect unique device IDs and restore the previously running service."""
    keyboard_ids = list(existing)
    if keyboard_ids:
        print("Уже настроены: " + ", ".join(keyboard_ids), flush=True)
        if not ask_add_keyboard():
            return keyboard_ids
    running = is_keyd_active()
    try:
        if running:
            run_command("sudo", "systemctl", "stop", "keyd")
        while True:
            print(
                "На добавляемой клавиатуре нажмите и отпустите левый, затем правый Ctrl.",
                flush=True,
            )
            monitor_keyboard(
                find_keyd_binary(), state_directory / "detected-keyboard-id"
            )
            device = (
                (state_directory / "detected-keyboard-id").read_text().strip().lower()
            )
            if device not in keyboard_ids:
                keyboard_ids.append(device)
            else:
                print("Эта клавиатура уже есть в списке.")
            if not ask_add_keyboard():
                return keyboard_ids
    finally:
        if running:
            run_command("sudo", "systemctl", "start", "keyd")


def monitor_keyboard(keyd_binary, target):
    """Identify a physical keyboard from both Ctrl press/release pairs."""
    # stdbuf makes a pipe event-driven; no polling or time-based detection.
    process = subprocess.Popen(
        ["sudo", "stdbuf", "-oL", keyd_binary, "monitor"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    chosen = None
    pressed = set()
    released = set()
    try:
        for line in process.stdout:
            match = re.search(
                r"^(.*?)\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4}(?::[0-9a-fA-F]+)?)\s+(leftcontrol|rightcontrol)\s+(down|up)\s*$",
                line,
            )
            if not match:
                continue
            name, device, key, event = match.groups()
            if device.startswith("0fac:") or "virtual" in name.lower():
                print(
                    "Получено виртуальное устройство. Остановите keyd и повторите установку.",
                    flush=True,
                )
                raise SystemExit(1)
            if chosen and chosen != device:
                print(
                    "Ctrl нажаты на разных устройствах. Повторите оба на одной клавиатуре.",
                    flush=True,
                )
                pressed.clear()
                released.clear()
            chosen = device
            if event == "down":
                pressed.add(key)
            elif key in pressed:
                released.add(key)
                print(f"{name.strip()}: {device}, {key} подтверждён", flush=True)
            if released == {"leftcontrol", "rightcontrol"}:
                with open(str(target), "w") as output_file:
                    output_file.write(device + "\n")
                break
        else:
            raise SystemExit("Монитор завершился без определения клавиатуры.")
    finally:
        process.terminate()
        process.wait()


def enable(record, previous=None):
    """Apply the managed configuration, rolling back on failure."""
    check_managed_state(previous if previous is not None else record)
    for key in ("sources", "xkb-options"):
        if not settings.is_writable(key):
            raise RuntimeError("Настройка заблокирована: " + key)
    snapshot = {
        "config": read_optional_text(keyd_config_path),
        "files": {n: read_optional_text(xkb_directory / n) for n in FILES},
        "options": list(get_setting("xkb-options")),
        "sources": get_setting("sources"),
        "active": is_keyd_active(),
        "service_enabled": subprocess.run(
            ["systemctl", "is-enabled", "--quiet", "keyd"], check=False
        ).returncode
        == 0,
    }
    try:
        write_xkb_files(FILES)
        validate_xkb(record)
        set_setting("sources", SOURCES)
        set_setting("xkb-options", record["options"])
        write_keyd_config(record["config"])
        run_command("sudo", "systemctl", "enable", "--now", "keyd")
        run_command("sudo", find_keyd_binary(), "reload")
        record["enabled"] = True
        save_state(record)
    except BaseException:
        write_keyd_config(snapshot["config"])
        write_xkb_files(snapshot["files"])
        set_setting("sources", snapshot["sources"])
        set_setting("xkb-options", snapshot["options"])
        if snapshot["active"]:
            run_command("sudo", "systemctl", "start", "keyd")
            run_command("sudo", find_keyd_binary(), "reload")
        else:
            run_command("sudo", "systemctl", "stop", "keyd")
        run_command(
            "sudo",
            "systemctl",
            "enable" if snapshot["service_enabled"] else "disable",
            "keyd",
        )
        raise
    print("Включено: отпускание левого Ctrl → EN, правого → RU, с индикатором GNOME.")


def disable(record, uninstall=False):
    """Disable mappings or restore the original installation baseline."""
    check_managed_state(record)
    write_keyd_config(record["config_before"] if uninstall else None)
    write_xkb_files(record["files_before"])
    set_setting("xkb-options", record["options_before"])
    set_setting("sources", record["sources_before"])
    refresh_keyd(record, restore=uninstall)
    record["enabled"] = False
    save_state(record)
    if uninstall:
        state_file.replace(state_directory / "removed.json")
        print(
            "Удалено. Исходные настройки восстановлены. Пакет keyd оставлен установленным."
        )
    else:
        print("Переключение одиночными Ctrl отключено.")


def check_environment():
    release = dict(
        line.split("=", 1)
        for line in Path("/etc/os-release").read_text().splitlines()
        if "=" in line
    )
    if (
        release.get("ID", "").strip('"') != "ubuntu"
        or release.get("VERSION_ID", "").strip('"') != "24.04"
    ):
        raise SystemExit("Нужна Ubuntu 24.04")
    if not re.search(
        r"GNOME Shell 46\.",
        subprocess.check_output(["gnome-shell", "--version"], text=True),
    ):
        raise SystemExit("Нужен GNOME Shell 46")
    if os.environ.get("XDG_SESSION_TYPE") != "wayland":
        raise SystemExit("Нужна сессия Wayland")


def prepare_installation():
    """Capture the original state and collect the requested keyboards."""
    if (state_directory / "installed").exists():
        raise SystemExit(
            "Обнаружена старая установка: сначала удалите её старым uninstall.sh"
        )
    enabled = (
        subprocess.run(
            ["systemctl", "is-enabled", "--quiet", "keyd"], check=False
        ).returncode
        == 0
    )
    service_was_active = is_keyd_active()
    if not find_keyd_binary():
        run_command("sudo", "apt-get", "update")
        run_command("sudo", "apt-get", "install", "-y", "software-properties-common")
        run_command("sudo", "add-apt-repository", "-y", "ppa:keyd-team/ppa")
        run_command("sudo", "apt-get", "update")
        run_command("sudo", "apt-get", "install", "-y", "keyd")
    if not find_keyd_binary():
        raise SystemExit("keyd не найден после установки")
    state_directory.mkdir(parents=True, exist_ok=True)
    state_directory.chmod(0o700)
    original = read_optional_text(keyd_config_path)
    files_before = {n: read_optional_text(xkb_directory / n) for n in FILES}
    options_before = list(get_setting("xkb-options"))
    migration = test_state_directory / "backup.json"
    if migration.exists():
        test = json.loads(migration.read_text())
        if (
            original != test["config_after"]
            or options_before != test["options_after"]
            or files_before != FILES
        ):
            raise SystemExit("Активный тест изменён; сначала выполните его undo")
        original = base64.b64decode(test["config_before"]).decode()
        options_before = test["options_before"]
        files_before = {n: None for n in FILES}
    elif any(v is not None for v in files_before.values()):
        # Adopt only the exact known configuration; its current state becomes
        # the rollback baseline when historical backups no longer exist.
        if files_before != FILES or not parse_keyboard_ids(original):
            raise SystemExit(
                "Пользовательские файлы XKB отличаются; они не перезаписаны"
            )
        print(
            "Принята существующая конфигурация. Удаление вернёт её текущее состояние."
        )
    # Existing legacy mappings may be adopted only if their body matches.
    legacy = (
        original.replace(
            "overload(control, macro(A-S-1))", "overload(control, f13)"
        ).replace("overload(control, macro(A-S-2))", "overload(control, f14)")
        if original
        else None
    )
    keyboard_ids = parse_keyboard_ids(legacy)
    keyboard_ids = collect_keyboards(keyboard_ids)
    config = build_keyd_config(keyboard_ids)
    record = {
        "version": 2,
        "config": config,
        "config_before": original,
        "files_before": files_before,
        "options_before": options_before,
        "options": [
            v for v in options_before if not v.startswith("grp:") and v != OPTION
        ]
        + ["grp:menu_toggle", OPTION],
        "sources_before": [list(x) for x in get_setting("sources")],
        "service_enabled": enabled,
        "service_active": service_was_active,
        "enabled": False,
    }
    # Keep the old configuration as a valid pre-apply state during migration.
    if migration.exists():
        record["config"] = test["config_after"]
    save_state(record)
    return record, config


def main(argv=None):
    """Dispatch the requested action from a desktop user session."""
    global settings
    arguments = sys.argv[1:] if argv is None else argv
    action = arguments[0] if arguments else "install"
    if os.geteuid() == 0:
        raise SystemExit("Запускайте обычным пользователем, без sudo.")
    if action not in ("install", "--enable", "--disable", "--uninstall"):
        raise SystemExit("Неизвестное действие")
    settings = Gio.Settings.new("org.gnome.desktop.input-sources")
    Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if action in ("install", "--enable"):
        check_environment()
    run_command("sudo", "-v")
    pending_config = None
    if state_file.exists():
        record = json.loads(state_file.read_text())
    else:
        if action != "install":
            raise SystemExit("Сначала запустите install.sh")
        record, pending_config = prepare_installation()
    if action in ("install", "--enable"):
        check_managed_state(record)
        previous = record
        if action == "install":
            config = pending_config
            if config is None:
                config = build_keyd_config(
                    collect_keyboards(parse_keyboard_ids(record["config"]))
                )
            record = dict(
                record, config=config, keyboard_ids=parse_keyboard_ids(config)
            )
        enable(record, previous)
        migration = test_state_directory / "backup.json"
        if migration.exists():
            migration.replace(test_state_directory / "migrated-to-v2.json")
    else:
        disable(record, uninstall=action == "--uninstall")


if __name__ == "__main__":
    main()
