#!/usr/bin/env bash
# Run as the desktop user from an Ubuntu GNOME Wayland terminal.
set -Eeuo pipefail
exec /usr/bin/python3 - "${1:-install}" <<'PYTHON'
import base64, ctypes as c, json, os, pathlib, re, shutil, subprocess, sys, tempfile
from gi.repository import Gio, GLib
P = pathlib.Path
ACTION = sys.argv[1]
if os.geteuid() == 0: raise SystemExit('Запускайте обычным пользователем, без sudo.')
if ACTION not in ('install','--enable','--disable','--uninstall'): raise SystemExit('Неизвестное действие')
state = P(os.environ.get('XDG_STATE_HOME', str(P.home()/'.local/state')))/'ctrl-layout-v2'
xroot = P(os.environ.get('XDG_CONFIG_HOME', str(P.home()/'.config')))/'xkb'
conf = P('/etc/keyd/ctrl-layout.conf')
teststate = state.parent/'ctrl-layout-conditional-test'
settings = Gio.Settings.new('org.gnome.desktop.input-sources')
Gio.bus_get_sync(Gio.BusType.SESSION, None)
recordfile = state/'state.json'
OPTION = 'ctrl_layout:conditional'
SOURCES = [['xkb','us'],['xkb','ru']]
FILES = {'rules/evdev': '! include %S/evdev\n\n! option = symbols\n  ctrl_layout:conditional = +ctrl_layout(conditional)\n', 'symbols/ctrl_layout': 'partial function_keys\nxkb_symbols "conditional" {\n replace key <FK13> {\n  type[Group1] = "ONE_LEVEL", type[Group2] = "ONE_LEVEL",\n  symbols[Group1] = [ VoidSymbol ], symbols[Group2] = [ ISO_Next_Group ],\n  actions[Group1] = [ NoAction() ], actions[Group2] = [ NoAction() ]\n };\n replace key <FK14> {\n  type[Group1] = "ONE_LEVEL", type[Group2] = "ONE_LEVEL",\n  symbols[Group1] = [ ISO_Next_Group ], symbols[Group2] = [ VoidSymbol ],\n  actions[Group1] = [ NoAction() ], actions[Group2] = [ NoAction() ]\n };\n replace key <MENU> { [ Menu ], actions[Group1] = [ NoAction() ] };\n};\n'}
def run(*args): subprocess.run(args,check=True)
def active(): return subprocess.run(['systemctl','is-active','--quiet','keyd']).returncode == 0
def binary(): return shutil.which('keyd.rvaiya') or shutil.which('keyd')
def read(path): return path.read_text() if path.exists() else None
def get(key): return settings.get_value(key).unpack()
def setval(key,value):
    typ = 'a(ss)' if key == 'sources' else 'as'
    if not settings.is_writable(key) or not settings.set_value(key,GLib.Variant(typ,value)):
        raise RuntimeError('Не удалось записать '+key)
    Gio.Settings.sync()
def save(r):
    state.mkdir(parents=True,exist_ok=True); state.chmod(0o700)
    tmp=state/'state.tmp'; tmp.write_text(json.dumps(r,ensure_ascii=False,indent=2)); tmp.replace(recordfile)
def system_config(value):
    if value is None:
        if conf.exists(): run('sudo','rm','--',str(conf))
    else:
        with tempfile.NamedTemporaryFile(mode='w',dir=state) as f:
            f.write(value); f.flush(); run('sudo','install','-m','644',f.name,str(conf))
def user_files(values):
    for name,value in values.items():
        p=xroot/name
        if value is None:
            if p.exists(): p.unlink()
        else:
            p.parent.mkdir(parents=True,exist_ok=True); p.write_text(value)
def check(r):
    if read(conf) not in (None,r['config'],r['config_before']):
        raise RuntimeError('Конфиг keyd изменён вручную; перезапись отменена.')
    for name,value in FILES.items():
        if read(xroot/name) not in (value,r['files_before'][name]):
            raise RuntimeError('Файл XKB изменён вручную: '+name)
    if list(get('xkb-options')) not in (r['options'],r['options_before']):
        raise RuntimeError('xkb-options изменены после установки; автоматическая перезапись отменена.')
    if [list(x) for x in get('sources')] not in (SOURCES,r['sources_before']):
        raise RuntimeError('Список раскладок изменён после установки; автоматическая перезапись отменена.')
def refresh(r, restore=False):
    others=[p for p in conf.parent.glob('*.conf') if p != conf]
    if conf.exists() or others:
        run('sudo','systemctl','start','keyd'); run('sudo',binary(),'reload')
    else: run('sudo','systemctl','stop','keyd')
    if restore:
        # Preserve service use by other keyd configurations.
        if not others:
            run('sudo','systemctl','enable' if r['service_enabled'] else 'disable','keyd')
            if not r['service_active']: run('sudo','systemctl','stop','keyd')
def validate():
    x=c.CDLL('libxkbcommon.so.0')
    class Names(c.Structure):
        _fields_=[(k,c.c_char_p) for k in ('rules','model','layout','variant','options')]
    for name,args,result in [('xkb_context_new',[c.c_int],c.c_void_p),('xkb_keymap_new_from_names',[c.c_void_p,c.POINTER(Names),c.c_int],c.c_void_p),('xkb_state_new',[c.c_void_p],c.c_void_p),('xkb_state_update_mask',[c.c_void_p]+[c.c_uint]*6,c.c_uint),('xkb_state_key_get_one_sym',[c.c_void_p,c.c_uint],c.c_uint),('xkb_state_update_key',[c.c_void_p,c.c_uint,c.c_int],c.c_uint),('xkb_state_serialize_layout',[c.c_void_p,c.c_uint],c.c_uint)]:
        f=getattr(x,name); f.argtypes=args; f.restype=result
    ctx=x.xkb_context_new(0)
    names=Names(b'evdev',b'pc105',b'us,ru',b'',','.join(r['options']).encode())
    km=x.xkb_keymap_new_from_names(ctx,c.byref(names),0)
    if not km: raise RuntimeError('Ошибка компиляции XKB')
    for group in (0,1):
        for target,key in ((0,191),(1,192)):
            st=x.xkb_state_new(km); x.xkb_state_update_mask(st,0,0,0,0,0,group)
            if x.xkb_state_key_get_one_sym(st,key)!=(0xfe08 if group!=target else 0xffffff):
                raise RuntimeError('Проверка условного переключения не пройдена')
            x.xkb_state_update_key(st,key,1); x.xkb_state_update_key(st,key,0)
            if x.xkb_state_serialize_layout(st,128)!=group: raise RuntimeError('Обнаружено прямое переключение XKB')
def monitor(binary,target):
    import subprocess, sys, re
    # stdbuf makes a pipe event-driven; no polling or time-based detection.
    p = subprocess.Popen(['sudo', 'stdbuf', '-oL', binary, 'monitor'],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    chosen = None
    pressed = set()
    released = set()
    try:
        for line in p.stdout:
            m = re.search(r'^(.*?)\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4}(?::[0-9a-fA-F]+)?)\s+(leftcontrol|rightcontrol)\s+(down|up)\s*$', line)
            if not m: continue
            name, device, key, event = m.groups()
            if device.startswith('0fac:') or 'virtual' in name.lower():
                print('Получено виртуальное устройство. Остановите keyd и повторите установку.', flush=True)
                raise SystemExit(1)
            if chosen and chosen != device:
                print('Ctrl нажаты на разных устройствах. Повторите оба на одной клавиатуре.', flush=True)
                pressed.clear(); released.clear()
            chosen = device
            if event == 'down': pressed.add(key)
            elif key in pressed:
                released.add(key)
                print(f'{name.strip()}: {device}, {key} подтверждён', flush=True)
            if released == {'leftcontrol', 'rightcontrol'}:
                with open(str(target), 'w') as f: f.write(device + '\n')
                break
        else: raise SystemExit('Монитор завершился без определения клавиатуры.')
    finally:
        p.terminate()
        p.wait()
def enable(r):
    check(r)
    for key in ('sources','xkb-options'):
        if not settings.is_writable(key): raise RuntimeError('Настройка заблокирована: '+key)
    snapshot={'config':read(conf),'files':{n:read(xroot/n) for n in FILES},'options':list(get('xkb-options')),'sources':get('sources'),'active':active(),'service_enabled':subprocess.run(['systemctl','is-enabled','--quiet','keyd']).returncode==0}
    try:
        user_files(FILES); validate()
        setval('sources',SOURCES); setval('xkb-options',r['options'])
        system_config(r['config']); run('sudo','systemctl','enable','--now','keyd'); run('sudo',binary(),'reload')
        r['enabled']=True; save(r)
    except BaseException:
        system_config(snapshot['config']); user_files(snapshot['files'])
        setval('sources',snapshot['sources']); setval('xkb-options',snapshot['options'])
        if snapshot['active']: run('sudo','systemctl','start','keyd'); run('sudo',binary(),'reload')
        else: run('sudo','systemctl','stop','keyd')
        run('sudo','systemctl','enable' if snapshot['service_enabled'] else 'disable','keyd')
        raise
    print('Включено: отпускание левого Ctrl → EN, правого → RU, с индикатором GNOME.')
def disable(r,uninstall=False):
    check(r)
    system_config(r['config_before'] if uninstall else None)
    user_files(r['files_before']); setval('xkb-options',r['options_before']); setval('sources',r['sources_before'])
    refresh(r,restore=uninstall)
    r['enabled']=False; save(r)
    if uninstall:
        recordfile.replace(state/'removed.json')
        print('Удалено. Исходные настройки восстановлены. Пакет keyd оставлен установленным.')
    else: print('Переключение одиночными Ctrl отключено.')

if ACTION in ('install','--enable'):
    release=dict(line.split('=',1) for line in P('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID','').strip('"')!='ubuntu' or release.get('VERSION_ID','').strip('"')!='24.04': raise SystemExit('Нужна Ubuntu 24.04')
    if not re.search(r'GNOME Shell 46\.',subprocess.check_output(['gnome-shell','--version'],text=True)): raise SystemExit('Нужен GNOME Shell 46')
    if os.environ.get('XDG_SESSION_TYPE')!='wayland': raise SystemExit('Нужна сессия Wayland')
run('sudo','-v')
if recordfile.exists():
    r=json.loads(recordfile.read_text())
else:
    if ACTION!='install': raise SystemExit('Сначала запустите install.sh')
    if (state.parent/'ctrl-layout'/'installed').exists(): raise SystemExit('Обнаружена старая установка: сначала удалите её старым uninstall.sh')
    enabled=subprocess.run(['systemctl','is-enabled','--quiet','keyd']).returncode==0
    wasactive=active()
    if not binary():
        run('sudo','apt-get','update'); run('sudo','apt-get','install','-y','software-properties-common')
        run('sudo','add-apt-repository','-y','ppa:keyd-team/ppa')
        run('sudo','apt-get','update'); run('sudo','apt-get','install','-y','keyd')
    if not binary(): raise SystemExit('keyd не найден после установки')
    state.mkdir(parents=True,exist_ok=True); state.chmod(0o700)
    original=read(conf); beforefiles={n:read(xroot/n) for n in FILES}; beforeoptions=list(get('xkb-options'))
    migration=teststate/'backup.json'
    if migration.exists():
        test=json.loads(migration.read_text())
        if original!=test['config_after'] or beforeoptions!=test['options_after'] or beforefiles!=FILES:
            raise SystemExit('Активный тест изменён; сначала выполните его undo')
        original=base64.b64decode(test['config_before']).decode()
        beforeoptions=test['options_before']; beforefiles={n:None for n in FILES}
    elif any(v is not None for v in beforefiles.values()):
        raise SystemExit('Пользовательские файлы XKB уже существуют; они не перезаписаны')
    running=active()
    try:
        if running: run('sudo','systemctl','stop','keyd')
        print('Нажмите и отпустите левый, затем правый Ctrl на нужной клавиатуре.',flush=True)
        monitor(binary(),state/'keyboard-id')
    finally:
        if running: run('sudo','systemctl','start','keyd')
    device=(state/'keyboard-id').read_text().strip()
    config=f'[ids]\n{device}\n\n[global]\noverload_tap_timeout = 0\n\n[main]\nleftcontrol = overload(control, f13)\nrightcontrol = overload(control, f14)\n'
    if original is not None:
        previous=config.replace('overload(control, f13)','overload(control, macro(A-S-1))').replace('overload(control, f14)','overload(control, macro(A-S-2))')
        compact=lambda v: re.sub(r'\s+','',v)
        if compact(original) not in (compact(config),compact(previous)): raise SystemExit('Существующий конфиг keyd отличается; он не перезаписан')
    r={'version':2,'config':config,'config_before':original,'files_before':beforefiles,'options_before':beforeoptions,'options':[v for v in beforeoptions if not v.startswith('grp:')]+['grp:menu_toggle',OPTION],'sources_before':[list(x) for x in get('sources')],'service_enabled':enabled,'service_active':wasactive,'enabled':False}
    # Accept the whitespace of the already active test configuration.
    if migration.exists(): r['config']=test['config_after']
    save(r)
if ACTION in ('install','--enable'):
    enable(r)
    migration=teststate/'backup.json'
    if migration.exists(): migration.replace(teststate/'migrated-to-v2.json')
else: disable(r,uninstall=ACTION=='--uninstall')
PYTHON
