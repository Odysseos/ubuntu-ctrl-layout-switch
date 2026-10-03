# Выбор английской и русской раскладки отпусканием выбранных клавиш

## Интерактивный установщик: выбор клавиш

Актуальный `install.sh` спрашивает первую и вторую клавиши непосредственно через `keyd monitor`: нажмите и отпустите каждую по запросу на одной клавиатуре. Поддерживаются оба Ctrl, оба Shift, оба Alt, обе Windows/Super. Одна и та же клавиша для двух раскладок отклоняется. Неподдерживаемые клавиши и одновременное нажатие двух клавиш не выбираются. Для каждой дополнительной клавиатуры выберите её собственную пару.

Повторная установка позволяет выбрать другую пару, сохраняя первоначальную резервную копию; `enable.sh` повторного выбора не требует. Каждое устройство хранит свою пару; изменение одного не меняет остальные. Далее ручная инструкция использует исходный проверенный вариант с Ctrl как пример.

Для правого Shift → US и левого Alt → RU замените две строки `[main]` на:

```ini
rightshift = overload(shift, f13)
leftalt = overload(alt, f14)
```

Для Ctrl слой — `control`, Shift — `shift`, левого Alt — `alt`, правого Alt — `altgr`, обеих Windows/Super (`leftmeta`, `rightmeta`) — `meta`. Файлы XKB остаются теми же. Одиночное действие Windows заменяется переключением; сочетания проверяйте отдельно.

Fn исключена из поддерживаемых клавиш: эксперимент не заработал на практике. Установщик отклоняет её при выборе.

## Результат и область проверки

На Ubuntu 24.04 с GNOME Shell 46 и Wayland:

- Нажать и отпустить **левый Ctrl** отдельно → английская раскладка.
- Нажать и отпустить **правый Ctrl** отдельно → русская раскладка.
- Повторное нажатие того же Ctrl оставляет выбранную раскладку.
- Пока Ctrl удерживается, переключения нет.
- Обычные сочетания, например Ctrl+C и Ctrl+V, сохраняются.
- Ввод и стандартный индикатор GNOME переключаются вместе.

Этот результат пользователь подтвердил в живой сессии. Решение использует keyd и пользовательскую конфигурацию XKB. Расширение GNOME, небезопасный режим Shell, отдельный скрипт на каждое нажатие и искусственные задержки не нужны.

Проверенная система: Ubuntu 24.04.5 LTS, GNOME Shell 46.0, Wayland, libxkbcommon 1.6.0, keyd 2.5.0 из PPA keyd-team. Мышь для этого механизма значения не имеет; проверенная клавиатура — Logitech MX Keys.

**Ограничения:** нужны ровно две обычные XKB-раскладки, в порядке US, RU. Сценарии «уже зажат Shift или буква, затем нажат и отпущен Ctrl» могут вызвать переключение: это обнаруженное и принятое ограничение `overload`. Поэтому абсолютное требование «никакая другая клавиша не удерживается» покрыто не полностью. Работа на экране блокировки отдельно не подтверждена.

Руководство описывает настройку с исходного состояния на другой системе. Если уже применён тест или комплект `install.sh`, повторно выполнять ручную установку поверх него не нужно: используйте его управление/откат. Все команды ниже выполняются в терминале графической сессии обычным пользователем; `sudo` указан только там, где необходим.

## 1. Проверить окружение

```bash
cat /etc/os-release
gnome-shell --version
printf '%s\n' "$XDG_SESSION_TYPE"
gsettings get org.gnome.desktop.input-sources sources
gsettings get org.gnome.desktop.input-sources xkb-options
```

Ожидаются Ubuntu 24.04, GNOME Shell 46.x и `wayland`. На другой версии совместимость нужно проверять, а не считать гарантированной.

Далее используются обычные каталоги `~/.config` и `~/.local/state`. Если переопределены `XDG_CONFIG_HOME` или `XDG_STATE_HOME`, замените соответствующие пути во всём руководстве.

## 2. Сохранить исходное состояние

Сделайте это **до изменений и только один раз**. Резервная копия должна отражать исходную систему, а не уже применённый эксперимент.

```bash
python3 - <<'PY'
import base64, json, pathlib, subprocess
from gi.repository import Gio
home = pathlib.Path.home()
backup = home / '.local/state/ctrl-layout-manual/backup.json'
if backup.exists():
    raise SystemExit('Резервная копия уже существует. Не перезаписывайте её повторной установкой.')
s = Gio.Settings.new('org.gnome.desktop.input-sources')
paths = [home/'.config/xkb/rules/evdev',
         home/'.config/xkb/symbols/ctrl_layout',
         pathlib.Path('/etc/keyd/ctrl-layout.conf')]
def status(*args):
    return subprocess.run(['systemctl', *args, 'keyd'],
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0
record = {
    'sources': s.get_value('sources').unpack(),
    'options': list(s.get_strv('xkb-options')),
    'service_active': status('is-active', '--quiet'),
    'service_enabled': status('is-enabled', '--quiet'),
    'files': {str(p): base64.b64encode(p.read_bytes()).decode()
              if p.exists() else None for p in paths},
}
backup.parent.mkdir(parents=True, exist_ok=True)
backup.parent.chmod(0o700)
backup.write_text(json.dumps(record, ensure_ascii=False, indent=2))
print(backup)
PY
```

Проверьте наличие пользовательских файлов:

```bash
ls -l ~/.config/xkb/rules/evdev ~/.config/xkb/symbols/ctrl_layout /etc/keyd/ctrl-layout.conf
```

Сообщения об отсутствующих файлах на чистой системе нормальны. Если файлы уже существуют, сначала разберите их содержимое: приведённые далее полные тексты нельзя бездумно вставлять поверх чужих настроек. Автоматический комплект при таком конфликте останавливается.

## 3. Установить keyd

Если keyd уже установлен, повторная установка не нужна. Найти исполняемый файл:

```bash
command -v keyd.rvaiya || command -v keyd
```

Для установки из использованного в этой задаче PPA:

```bash
sudo apt-get update
sudo apt-get install software-properties-common
sudo add-apt-repository ppa:keyd-team/ppa
sudo apt-get update
sudo apt-get install keyd
```

В проверенной сборке пакет называется `keyd`, служба — `keyd`, а исполняемый файл — **`keyd.rvaiya`**. Поэтому команда `keyd` могла выдавать «команда не найдена», хотя пакет был установлен. В других сборках имя может быть `keyd`.

Для последующих команд в этом терминале:

```bash
KEYD_BIN=$(command -v keyd.rvaiya || command -v keyd)
```

## 4. Определить ID клавиатуры

Нужен ID **устройства**, а не код клавиши. Последняя часть ID помогает отличить клавиатурный интерфейс MX Keys от интерфейса Mouse той же клавиатуры.

Запустите монитор. Блок временно останавливает работающую службу и восстанавливает её после завершения монитора:

```bash
(
    keyd_was_active=no
    if systemctl is-active --quiet keyd; then
        keyd_was_active=yes
    fi
    trap 'if [ "$keyd_was_active" = yes ]; then sudo systemctl start keyd; fi' EXIT
    sudo systemctl stop keyd
    sudo "$KEYD_BIN" monitor
)
```

Для приведённого далее примера нажмите и отпустите левый и правый Ctrl на нужной клавиатуре; при другой выбранной паре используйте её. Затем завершите монитор Ctrl+C.

В нашей системе вывод был таким:

```text
MX Keys Keyboard  046d:b35b:794355b5  leftcontrol down
MX Keys Keyboard  046d:b35b:794355b5  leftcontrol up
MX Keys Keyboard  046d:b35b:794355b5  rightcontrol down
MX Keys Keyboard  046d:b35b:794355b5  rightcontrol up
```

Здесь `046d:b35b:794355b5` — ID клавиатуры, `leftcontrol` и `rightcontrol` — имена клавиш. Не выбирайте `keyd virtual keyboard`, `keyd virtual pointer` или `MX Keys Mouse`.

**На другой системе используйте фактический ID из своего вывода.** Повторите определение для каждой клавиатуры. Проверьте также остальные `/etc/keyd/*.conf`: они не должны независимо перехватывать то же устройство.

## 5. Создать конфигурацию keyd

Откройте файл системным редактором через административный доступ:

```bash
gedit admin:///etc/keyd/ctrl-layout.conf
```

Для нашей MX Keys полный текст:

```ini
[ids]
046d:b35b:794355b5

[global]
overload_tap_timeout = 0

[main]
leftcontrol = overload(control, f13)
rightcontrol = overload(control, f14)
```

Для разных назначений нужны **отдельные конфиги keyd**, каждый с одним ID. Например, внешняя клавиатура — `/etc/keyd/ctrl-layout-046d-b35b-794355b5.conf`:

```ini
[ids]
046d:b35b:794355b5

[global]
overload_tap_timeout = 0

[main]
leftcontrol = overload(control, f13)
rightcontrol = overload(control, f14)
```

Встроенная — `/etc/keyd/ctrl-layout-0001-0001-09b4e68d.conf`:

```ini
[ids]
0001:0001:09b4e68d

[global]
overload_tap_timeout = 0

[main]
leftcontrol = overload(control, f13)
rightshift = overload(shift, f14)
```

ID приведены из этой задачи; на другой машине определите их заново. Один ID не должен одновременно оставаться в старом общем конфиге и новом отдельном. Перед ручным переходом сохраните исходные файлы и уберите пересекающиеся назначения. Приведённый ниже ручной откат восстанавливает исходный пример с одним `ctrl-layout.conf`; дополнительные файлы также нужно сохранить/удалить отдельно. Автоматический комплект учитывает все свои файлы и выполняет полный откат сам.

Список источников и файлы XKB остаются общими. Отдельные конфиги дают устройствам независимое состояние keyd; сочетания между разными физическими клавиатурами отдельно не проверены.

`overload(control, f13)` использует слой Control при удержании, а F13 выдаёт при одиночном нажатии с отпусканием. Аналогично работает F14. Именно семантика `overload`, а не отдельный параметр `keyup`, связывает дополнительное действие с завершённым одиночным нажатием.

`overload_tap_timeout = 0` отключает ограничение продолжительности такого нажатия: долго удержанный Ctrl без других клавиш тоже переключит раскладку при отпускании. Это не пауза и не задержка выполнения. Используется обычный `overload`, не `overloadt`.

При физическом отпускании Ctrl keyd выдаёт виртуальное нажатие F13/F14. Поэтому Mutter может обрабатывать **нажатие F13/F14**, а пользователь всё равно получает переключение именно по **отпусканию физического Ctrl**. См. также установленную справку `man keyd.rvaiya` либо `man keyd`.

## 6. Создать пользовательскую таблицу XKB

```bash
mkdir -p ~/.config/xkb/rules ~/.config/xkb/symbols
gedit ~/.config/xkb/rules/evdev
```

Содержимое `~/.config/xkb/rules/evdev`:

```text
! include %S/evdev

! option = symbols
  ctrl_layout:conditional = +ctrl_layout(conditional)
```

Первая строка подключает системные правила. Новая опция добавляет нашу секцию символов. Системные файлы в `/usr/share/X11/xkb` изменять не нужно.

Откройте файл символов:

```bash
gedit ~/.config/xkb/symbols/ctrl_layout
```

Его полный текст:

```text
partial function_keys
xkb_symbols "conditional" {
    replace key <FK13> {
        type[Group1] = "ONE_LEVEL",
        type[Group2] = "ONE_LEVEL",
        symbols[Group1] = [ VoidSymbol ],
        symbols[Group2] = [ ISO_Next_Group ],
        actions[Group1] = [ NoAction() ],
        actions[Group2] = [ NoAction() ]
    };
    replace key <FK14> {
        type[Group1] = "ONE_LEVEL",
        type[Group2] = "ONE_LEVEL",
        symbols[Group1] = [ ISO_Next_Group ],
        symbols[Group2] = [ VoidSymbol ],
        actions[Group1] = [ NoAction() ],
        actions[Group2] = [ NoAction() ]
    };
    replace key <MENU> {
        [ Menu ],
        actions[Group1] = [ NoAction() ]
    };
};
```

`Group1` здесь соответствует источнику с индексом 0, `Group2` — индексу 1. F13/F14 используются как промежуточные клавиши: физическое наличие таких клавиш на клавиатуре не требуется.

`VoidSymbol` намеренно задаёт пустой результат. Во время изолированной проверки вариант с `NoSymbol` оставлял у F13 унаследованный символ; поэтому в рабочем варианте используется именно `VoidSymbol`.

## 7. Задать источники и включить опции

Команды сохраняют остальные XKB-опции, но заменяют старые `grp:*`. Прежние значения уже находятся в резервной копии.

```bash
python3 - <<'PY'
from gi.repository import Gio, GLib
s = Gio.Settings.new('org.gnome.desktop.input-sources')
for key in ('sources', 'xkb-options'):
    if not s.is_writable(key):
        raise SystemExit('Настройка заблокирована: ' + key)
options = [v for v in s.get_strv('xkb-options')
           if not v.startswith('grp:') and v != 'ctrl_layout:conditional']
options += ['grp:menu_toggle', 'ctrl_layout:conditional']
if not s.set_value('sources', GLib.Variant('a(ss)', [('xkb', 'us'), ('xkb', 'ru')])):
    raise SystemExit('Не удалось задать источники')
if not s.set_strv('xkb-options', options):
    raise SystemExit('Не удалось задать опции')
Gio.Settings.sync()
PY
```

Почему присутствует `grp:menu_toggle`, хотя Menu нам не нужна? В Mutter 46 штатный обработчик `ISO_Next_Group` включается для известных опций переключения группы. Эта опция включает нужный путь; наша секция, подключённая после неё, возвращает клавише Menu обычный символ и убирает её действие переключения. CapsLock не переназначается.

## 8. Включить службу и проверить

```bash
sudo systemctl enable --now keyd
sudo "$KEYD_BIN" reload
```

Выберите английскую раскладку через меню стандартного индикатора GNOME. Это даёт заведомо согласованное исходное состояние, особенно после предыдущих экспериментов с прямым переключением XKB.

Откройте gedit и проверьте:

| Действие | Ожидание |
|---|---|
| Удерживать Ctrl без отпускания | Раскладка не меняется |
| Отпустить одиночный правый Ctrl | RU в индикаторе и русский ввод |
| Повторить правый Ctrl | RU остаётся |
| Нажать и отпустить левый Ctrl | EN в индикаторе и английский ввод |
| Повторить левый Ctrl | EN остаётся |
| Ctrl+C, Ctrl+V | Обычное действие, без переключения |
| Выбрать язык через меню, затем нажать Ctrl | Выбирается язык, соответствующий стороне Ctrl |
| Перейти в другое окно и повторить | Ввод и индикатор согласованы |

Перелогин для применения в проверенной сессии не потребовался. Проверка сохранения поведения после нового входа полезна как отдельный приёмочный тест.

## Почему переключение синхронизировано с индикатором

Схема рабочего решения:

```text
Отпускание физического Ctrl
    → keyd выдаёт F13 или F14
    → символ зависит от текущей группы XKB
        → уже нужная группа: VoidSymbol
        → другая группа: ISO_Next_Group
    → Mutter вызывает штатный обработчик GNOME Shell
    → GNOME Shell активирует источник и обновляет индикатор
```

| Текущий источник | F13 от левого Ctrl | F14 от правого Ctrl |
|---|---|---|
| 0 / US | VoidSymbol: остаться | ISO_Next_Group: перейти в RU |
| 1 / RU | ISO_Next_Group: перейти в US | VoidSymbol: остаться |

Это выбор конкретного источника, построенный на условном циклическом переключении. Для двух источников «следующий» из неправильного состояния всегда означает нужный. Для трёх и более это рассуждение перестаёт работать.

Mutter 46 пересобирает назначения при сигнале `keymap-layout-group-changed`, учитывая активную группу. Поэтому клавиша с `ISO_Next_Group` в одной группе не становится безусловным переключателем в обеих. Ключевые места исходника: `reload_active_keyboard_layouts`, `reload_iso_next_group_combos`, `process_iso_next_group`, подключение `reload_keybindings`. [Исходник Mutter 46](https://github.com/GNOME/mutter/blob/46.0/src/core/keybindings.c).

`NoAction()` принципиален: XKB не должен самостоятельно менять группу. GNOME Shell получает штатный сигнал `modifiers-accelerator-activated`; `_modifiersSwitcher()` выбирает следующий источник и вызывает его активацию. Этот путь обновляет и раскладку, и состояние, за которым следит индикатор. [Исходник GNOME Shell 46](https://github.com/GNOME/gnome-shell/blob/46.0/js/ui/status/keyboard.js).

## Какие другие варианты рассматривались

Ниже отдельно отмечены реальные испытания и исследованные альтернативы. «Не подошло» не обязательно означает неисправность программы: иногда вариант нарушал требование обойтись без расширения или менял слишком много в системе.

### Испытано в ходе этой задачи

| Вариант | Результат и причина отказа |
|---|---|
| keyd → Alt+Shift+1/2 → Input Source Binder | Рабочий ранний вариант. Расширение регистрирует сочетания и вызывает `getInputSourceManager().inputSources[index].activate()`. Не подошла зависимость от расширения GNOME. |
| `overload` и заранее удерживаемые Shift/буква | Выявлены ложные одиночные нажатия Ctrl. Это ограничение не устранено; пользователь согласился принять эти редкие случаи. |
| keyd → F13/F14 → `ISO_First_Group`/`ISO_Last_Group` + `LockGroup(group=1/2)` | **Ввод переключался, индикатор — нет**, что пользователь подтвердил. Группа менялась напрямую в XKB, обходя выбранный источник GNOME Shell. Поэтому вариант отменён. |
| Условные F13/F14 с `NoSymbol` | В изолированном тесте libxkbcommon сохранила унаследованный символ F13 вместо пустого результата. Заменено на `VoidSymbol`. Это не испытание в живой сессии. |
| Условные F13/F14 с `ISO_Next_Group`, `VoidSymbol`, `NoAction()` | Пользователь подтвердил работу ввода и индикатора. Этот вариант стал итоговым. |

### Исследовано по коду и документации; не выдаётся за успешный тест в сессии

| Вариант | Почему не выбран |
|---|---|
| Обычный переключатель «следующая раскладка» | Без условия не обеспечивает «левый всегда EN, правый всегда RU»: повторное нажатие меняет язык обратно. В итоговом решении добавлено условие на уровне XKB. |
| `gsettings ... current` | Устаревшая настройка не даёт нужного управления текущим источником GNOME 46. |
| Перестановка `sources`, изменение MRU | Источники и история их использования не равны команде активации. Простое переупорядочение не гарантирует выбор; замена списка единственным источником меняет меню и весь набор доступных раскладок. |
| `org.gnome.Shell.Eval` с вызовом `activate()` | Прямой вызов логически подходит, но обычный внешний Eval ограничен в GNOME 46 и требует unsafe mode. Старый пример с `imports.ui...` также не соответствует переходу Shell на ES modules. Небезопасный режим ради раскладки не принят. |
| Собственный C/C++-бинарник с libxkbcommon | Библиотека изменяет состояние XKB, созданное в своём процессе. Это не даёт доступа к состоянию композитора и объекту InputSourceManager внутри GNOME Shell. Сам по себе другой язык программирования не устраняет границу процессов. |
| `ibus engine ...` | Для обычных XKB-источников GNOME использует общий вспомогательный IBus engine. Выбор IBus engine не является выбором группы XKB GNOME. |
| g3kb-switch | Умеет выбирать источник по индексу, но в современных GNOME требует вспомогательное расширение либо небезопасный Eval. Не решает требование «без расширения». [Документация проекта](https://github.com/lyokha/g3kb-switch). |
| Shyriiwook, Input Source D-Bus Interface, Agism | Добавляют нужный внешний интерфейс через расширение GNOME. По той же причине не выбраны. |
| Tapper | Для согласованного управления GNOME использует Agism. Его документация отдельно описывает рассинхронизацию индикатора при обходе GNOME через XKB; это подтверждение архитектурной проблемы, не тест Tapper в нашей сессии. [Документация Tapper](https://kbd-tapper.sourceforge.io/tapper.en.html). |
| xkb-switch, kbdd и другие X11-инструменты | Не предоставляют нужное управление всей Wayland-сессией GNOME. Доступ к Xwayland не равен управлению источником GNOME. |
| Fcitx5 / fcitx5-remote | Другой фреймворк ввода; означал бы переход на другую систему управления вводом. Пользователь отверг этот вариант. |
| wtype / virtual-keyboard protocol | Протокол виртуальной клавиатуры, применяемый в таких решениях, не даёт готового пути для Mutter 46; нельзя переносить решения для wlroots на GNOME без проверки поддержки. |
| Mutter RemoteDesktop / EIS | В исследованных интерфейсах найдена передача клавиатурных событий, но не готовая команда выбора `inputSources[index]`. Реальная интроспекция D-Bus из среды агента была заблокирована песочницей; это не доказательство отсутствия интерфейса само по себе. |
| CapsLock как переключатель или промежуточная клавиша | Пользователь явно исключил использование CapsLock. В окончательной конфигурации он сохранён. |
| Паузы, sleep, таймеры для синхронизации | Не использованы: пользователь исключил такие обходы. Работа построена на событиях и штатной обработке GNOME. |

Исследование не доказывает отсутствие вообще всех мыслимых внешних API или приложений. Оно объясняет, почему перечисленные проверенные направления не стали выбранным решением.

## Ручной откат

Этот раздел относится только к резервной копии из шага 2. Он восстанавливает сохранённые файлы и настройки целиком: если после установки вы вносили дополнительные изменения, сначала сравните их с копией. Не применяйте этот откат поверх установки, управляемой четырьмя скриптами.

```bash
sudo -v
python3 - <<'PY'
import base64, json, pathlib, shutil, subprocess, tempfile
from gi.repository import Gio, GLib
backup = pathlib.Path.home()/'.local/state/ctrl-layout-manual/backup.json'
r = json.loads(backup.read_text())
for name, encoded in r['files'].items():
    p = pathlib.Path(name)
    content = base64.b64decode(encoded) if encoded is not None else None
    if name == '/etc/keyd/ctrl-layout.conf':
        if content is None:
            subprocess.run(['sudo', 'rm', '-f', '--', name], check=True)
        else:
            with tempfile.NamedTemporaryFile() as f:
                f.write(content); f.flush()
                subprocess.run(['sudo', 'install', '-m', '644', f.name, name], check=True)
    elif content is None:
        p.unlink(missing_ok=True)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
s = Gio.Settings.new('org.gnome.desktop.input-sources')
if not s.set_value('sources', GLib.Variant('a(ss)', r['sources'])):
    raise SystemExit('Не удалось восстановить sources')
if not s.set_strv('xkb-options', r['options']):
    raise SystemExit('Не удалось восстановить xkb-options')
Gio.Settings.sync()
keyd = shutil.which('keyd.rvaiya') or shutil.which('keyd')
other = [p for p in pathlib.Path('/etc/keyd').glob('*.conf')
         if p.name != 'ctrl-layout.conf']
if other:
    print('Есть другие конфиги keyd: служба сохраняется работающей.')
    subprocess.run(['sudo', 'systemctl', 'start', 'keyd'], check=True)
    subprocess.run(['sudo', keyd, 'reload'], check=True)
else:
    subprocess.run(['sudo', 'systemctl',
                    'enable' if r['service_enabled'] else 'disable', 'keyd'], check=True)
    subprocess.run(['sudo', 'systemctl',
                    'start' if r['service_active'] else 'stop', 'keyd'], check=True)
    if r['service_active']:
        subprocess.run(['sudo', keyd, 'reload'], check=True)
print('Исходное состояние восстановлено. Резервная копия сохранена.')
PY
```

keyd и PPA этим откатом не удаляются. Если до эксперимента существовали назначения через Input Source Binder, они также вернутся вместе со старым конфигом. Это восстановление исходного состояния, а не обязательно отключение всех прежних способов переключения.

## Связь с готовыми скриптами

В этой же папке находятся `ctrl-layout.py`, четыре обёртки `install.sh`, `enable.sh`, `disable.sh`, `uninstall.sh` и краткий `README.md`. Вся логика находится в Python-файле; каждая обёртка запускает его напрямую через `/usr/bin/python3` с нужным режимом. Они автоматизируют этот механизм, определяют ID через монитор, проверяют таблицу XKB, сохраняют состояние и обнаруживают конфликты. Переносите папку целиком. Установщик позволяет нажатием выбрать две разные поддерживаемые клавиши, затем последовательно добавить несколько клавиатур и дополнять список повторным запуском; enable.sh включает уже сохранённый набор.

Ручная инструкция и автоматический комплект используют разные резервные копии. Выберите один способ управления установкой и используйте соответствующий ему откат.
