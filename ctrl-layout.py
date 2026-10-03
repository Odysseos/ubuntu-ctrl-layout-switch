#!/usr/bin/python3
"""Manage modifier-release layout selection in Ubuntu GNOME Wayland sessions."""

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


DEFAULT_KEYS = ("leftcontrol", "rightcontrol")
KEY_LAYERS = {
    "leftcontrol": "control",
    "rightcontrol": "control",
    "leftshift": "shift",
    "rightshift": "shift",
    "leftalt": "alt",
    "rightalt": "altgr",
    "leftmeta": "meta",
    "rightmeta": "meta",
}


def selected_keys(first, second, current=DEFAULT_KEYS):
    keys = (first or current[0], second or current[1])
    if any(key not in KEY_LAYERS for key in keys) or keys[0] == keys[1]:
        raise ValueError("Для двух раскладок нужны две разные поддерживаемые клавиши.")
    return keys


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
    write_config_file(keyd_config_path, value)


def write_config_file(path, value):
    """Write a single owned keyd configuration."""
    if value is None:
        if path.exists():
            run_command("sudo", "rm", "--", str(path))
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=state_directory
        ) as temporary_file:
            temporary_file.write(value)
            temporary_file.flush()
            run_command("sudo", "install", "-m", "644", temporary_file.name, str(path))


def config_path(name):
    if not re.fullmatch(r"ctrl-layout(?:-[0-9a-f-]+)?\.conf", name):
        raise RuntimeError("Недопустимое имя управляемого конфига: " + name)
    return keyd_config_path.parent / name


def config_values(record, original=False):
    if "configs" in record:
        return record["configs_before" if original else "configs"]
    return {keyd_config_path.name: record["config_before" if original else "config"]}


def write_configs(values):
    for name, value in values.items():
        path = config_path(name)
        if path == keyd_config_path:
            write_keyd_config(value)
        else:
            write_config_file(path, value)


def keyboard_profiles(record):
    if "keyboards" in record:
        return {device: tuple(keys) for device, keys in record["keyboards"].items()}
    ids, keys = parse_keyd_config(record["config"])
    return dict.fromkeys(ids, keys)


def configure_keyboards(existing):
    """Edit only the device the user presses; retain other device mappings."""
    profiles = dict(existing)
    for device, keys in profiles.items():
        print(f"Сохранено: {device}: {keys[0]} → US, {keys[1]} → RU", flush=True)
    while True:
        print(
            "На клавиатуре, которую хотите настроить, выберите свою пару клавиш.",
            flush=True,
        )
        device, keys = choose_layout_keys()
        profiles[device] = keys
        if not ask_add_keyboard():
            return profiles


def with_keyboard_profiles(record, profiles):
    """Migrate a shared config to per-device files, keeping the initial backup."""
    result = dict(record)
    before = dict(config_values(record, original=True))
    configs = dict.fromkeys(config_values(record), None)
    for device, keys in profiles.items():
        if not re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{4}(?::[0-9a-f]+)?", device):
            raise RuntimeError("Недопустимый ID клавиатуры: " + device)
        name = "ctrl-layout-" + device.replace(":", "-") + ".conf"
        # A pre-existing file with this name will be rejected by check_managed_state.
        before.setdefault(name, None)
        configs[name] = build_keyd_config([device], keys)
    result.update(
        version=3,
        keyboards={device: list(keys) for device, keys in profiles.items()},
        configs=configs,
        configs_before=before,
    )
    result.pop("layout_keys", None)
    result.pop("keyboard_ids", None)
    return result


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
    for name, expected in config_values(record).items():
        if read_optional_text(config_path(name)) not in (
            None,
            expected,
            config_values(record, original=True).get(name),
        ):
            raise RuntimeError("Конфиг keyd изменён вручную: " + name)
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
        p
        for p in keyd_config_path.parent.glob("*.conf")
        if p.name not in config_values(record)
    ]
    if any(config_path(name).exists() for name in config_values(record)) or others:
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


def parse_keyd_config(text):
    """Recognize only configurations produced by this installer."""
    if text is None:
        return [], DEFAULT_KEYS
    header = re.match(r"\s*\[ids\]\s*\n(.*?)\n\[global\]", text, re.DOTALL)
    bindings = re.findall(
        r"^(\w+)\s*=\s*overload\(\w+,\s*f(13|14)\)\s*$", text, re.MULTILINE
    )
    if (
        not header
        or len(bindings) != 2
        or {number for _, number in bindings} != {"13", "14"}
    ):
        raise RuntimeError(
            "Существующий конфиг не соответствует управляемым назначениям."
        )
    by_target = {number: key for key, number in bindings}
    try:
        keys = selected_keys(by_target["13"], by_target["14"])
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    ids = header[1].split()
    if not ids or any(
        not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}(?::[0-9a-fA-F]+)?", key)
        for key in ids
    ):
        raise RuntimeError("В конфиге ожидаются явные ID клавиатур.")
    if re.sub(r"\s+", "", text) != re.sub(r"\s+", "", build_keyd_config(ids, keys)):
        raise RuntimeError("Конфиг содержит посторонние настройки или неверные слои.")
    return list(dict.fromkeys(key.lower() for key in ids)), keys


def parse_keyboard_ids(text):
    return parse_keyd_config(text)[0]


def build_keyd_config(keyboard_ids, keys=DEFAULT_KEYS):
    keys = selected_keys(*keys)
    config = "[ids]\n" + "\n".join(keyboard_ids)
    config += "\n\n[global]\noverload_tap_timeout = 0\n\n[main]\n"
    for key, target in zip(keys, ("f13", "f14")):
        config += f"{key} = overload({KEY_LAYERS[key]}, {target})\n"
    return config


def capture_layout_keys(keyd_binary):
    """Capture two distinct supported keys, with complete down/up pairs."""
    print(
        "Нажмите и отпустите клавишу для первой раскладки (US). "
        "Поддерживаются Ctrl, Shift, Alt, Windows/Super с любой стороны. Ctrl+C — отмена.",
        flush=True,
    )
    process = subprocess.Popen(
        ["sudo", "stdbuf", "-oL", keyd_binary, "monitor"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    selected = []
    selected_device = None
    pending = None
    held = set()
    try:
        for line in process.stdout:
            match = re.search(
                r"^(.*?)\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4}(?::[0-9a-fA-F]+)?)\s+(\S+)\s+(down|up)\s*$",
                line,
            )
            if not match:
                continue
            name, device, key, event = match.groups()
            device = device.lower()
            if device.startswith("0fac:") or "virtual" in name.lower():
                raise RuntimeError(
                    "Получено виртуальное устройство вместо физической клавиатуры."
                )
            token = (device, key)
            if event == "down":
                if token in held:
                    continue
                held.add(token)
                if len(held) != 1:
                    pending = None
                    print("Отпустите все клавиши и нажмите одну отдельно.", flush=True)
                elif key not in KEY_LAYERS:
                    pending = None
                    print(
                        f"Клавиша {key} не поддерживается. Выберите Ctrl, Shift, Alt, Windows/Super.",
                        flush=True,
                    )
                elif selected_device and device != selected_device:
                    pending = None
                    print("Выберите вторую клавишу на той же клавиатуре.", flush=True)
                else:
                    pending = token
                continue
            held.discard(token)
            if pending != token:
                continue
            pending = None
            if key in selected:
                print(
                    "Эта клавиша уже назначена первой раскладке. Нажмите другую.",
                    flush=True,
                )
                continue
            selected_device = device
            selected.append(key)
            if len(selected) == 2:
                print(
                    f"Выбрано: {selected[0]} → US, {selected[1]} → RU ({device}).",
                    flush=True,
                )
                return device, tuple(selected)
            print(
                f"Первая клавиша: {key}. Нажмите и отпустите другую клавишу для второй раскладки (RU).",
                flush=True,
            )
        raise RuntimeError("Монитор завершился до выбора двух клавиш.")
    finally:
        process.terminate()
        process.wait()


def choose_layout_keys():
    """Temporarily release keyd's grab to observe the physical keys."""
    running = is_keyd_active()
    try:
        if running:
            run_command("sudo", "systemctl", "stop", "keyd")
        return capture_layout_keys(find_keyd_binary())
    finally:
        if running:
            run_command("sudo", "systemctl", "start", "keyd")


def confirmation_hint():
    """Accept both languages without relying on Xwayland's keyboard state."""
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


def enable(record, previous=None):
    """Apply the managed configuration, rolling back on failure."""
    baseline = previous if previous is not None else record
    check_managed_state(baseline)
    for name in config_values(record).keys() - config_values(baseline).keys():
        if config_path(name).exists():
            raise RuntimeError(
                "Файл уже существует и не принадлежит установке: " + name
            )
    for key in ("sources", "xkb-options"):
        if not settings.is_writable(key):
            raise RuntimeError("Настройка заблокирована: " + key)
    snapshot = {
        "configs": {
            name: read_optional_text(config_path(name))
            for name in config_values(record)
        },
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
        write_configs(config_values(record))
        run_command("sudo", "systemctl", "enable", "--now", "keyd")
        run_command("sudo", find_keyd_binary(), "reload")
        record["enabled"] = True
        save_state(record)
    except BaseException:
        write_configs(snapshot["configs"])
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
    for device, keys in keyboard_profiles(record).items():
        print(f"Включено: {device}: {keys[0]} → EN, {keys[1]} → RU по отпусканию.")


def disable(record, uninstall=False):
    """Disable mappings or restore the original installation baseline."""
    check_managed_state(record)
    write_configs(
        config_values(record, original=True)
        if uninstall
        else dict.fromkeys(config_values(record), None)
    )
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
        print("Переключение одиночными клавишами отключено.")


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
    keyboard_ids, keys = parse_keyd_config(legacy)
    profiles = configure_keyboards(dict.fromkeys(keyboard_ids, keys))
    # Keep the original single-file state until the multi-file transaction succeeds.
    config = original
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
    return record, profiles


def main(argv=None):
    """Dispatch the requested action from a desktop user session."""
    global settings
    arguments = sys.argv[1:] if argv is None else argv
    action = arguments[0] if arguments else "install"
    if len(arguments) > 1:
        raise SystemExit("Клавиши выбираются нажатием в диалоге, без параметров.")
    if os.geteuid() == 0:
        raise SystemExit("Запускайте обычным пользователем, без sudo.")
    if action not in ("install", "--enable", "--disable", "--uninstall"):
        raise SystemExit("Неизвестное действие")
    settings = Gio.Settings.new("org.gnome.desktop.input-sources")
    Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if action in ("install", "--enable"):
        check_environment()
    run_command("sudo", "-v")
    pending_profiles = None
    if state_file.exists():
        record = json.loads(state_file.read_text())
    else:
        if action != "install":
            raise SystemExit("Сначала запустите install.sh")
        record, pending_profiles = prepare_installation()
    if action in ("install", "--enable"):
        check_managed_state(record)
        previous = record
        if action == "install":
            profiles = pending_profiles
            if profiles is None:
                profiles = configure_keyboards(keyboard_profiles(record))
            record = with_keyboard_profiles(record, profiles)
        enable(record, previous)
        migration = test_state_directory / "backup.json"
        if migration.exists():
            migration.replace(test_state_directory / "migrated-to-v2.json")
    else:
        disable(record, uninstall=action == "--uninstall")


if __name__ == "__main__":
    main()
