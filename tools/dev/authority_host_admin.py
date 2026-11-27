"""Host admin menu for a dedicated authority server.

A private web page lets the server host see and kick pilots, hold or assign
Company ships, and edit the server world: money, ships, faction relations,
blueprints, building resources and research.

- The page listens on 127.0.0.1 only and every API call needs the host token.
  Reach it from another computer through an SSH tunnel.
- X4 edits run on the supervisor thread, one command at a time, and only while
  the world runs with no save, trade or combat write pending.
- Pilots see world edits after their next join, since each join loads the
  newest checkpoint.
"""
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import stat
import threading
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET


_WARE = re.compile(r'[a-z][a-z0-9_]{0,95}\Z')
_FACTION = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}\Z')
_NAME = re.compile(r'[ -{}~]{1,48}\Z')  # printable ASCII without '|'
_TOKEN = re.compile(r'[A-Za-z0-9_-]{24,128}\Z')
_TEXT_REF = re.compile(r'\{(\d+),\s*(\d+)\}')
_MAX_PACKET = 768
_MAX_WARES_PER_COMMAND = 40
_MAX_BODY = 16384
_MAX_ROWS = 4096
_LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1'}
BLUEPRINT_GROUPS = ('modules', 'ships', 'equipment', 'mods')
_NO_BLUEPRINT = {'noplayerblueprint', 'noblueprint', 'deprecated', 'missiononly'}
_X4_ACTIONS = {'money', 'relation', 'research', 'blueprints', 'ship_repair', 'ship_rename', 'ship_cargo',
               'station_fill', 'station_add', 'refresh'}
_SESSION_ACTIONS = {'kick', 'hold', 'assign', 'save'}
HOOK_TICK = ' local ok,reason=bridge:update();assert(ok,reason)\n'
HOOK_ENTRY = '    <file name="ui/authority_world_bridge_probe.lua" />\n'


# --- Game catalogue -------------------------------------------------------

def _catalog_files(game, wanted):
    """Return {name: bytes} for wanted files, the last catalogue winning."""
    found = {}
    for cat in sorted(Path(game).glob('[0-9][0-9].cat')):
        offset = 0
        for line in cat.read_text(encoding='utf-8', errors='replace').splitlines():
            parts = line.rsplit(' ', 3)
            if len(parts) != 4 or not parts[1].isdigit():
                continue
            name, size = parts[0], int(parts[1])
            if name in wanted:
                found[name] = (cat.with_suffix('.dat'), offset, size)
            offset += size
    contents = {}
    for name, (dat, offset, size) in found.items():
        with dat.open('rb') as source:
            source.seek(offset)
            contents[name] = source.read(size)
    return contents


def _resolve_text(texts, value, depth=0):
    if not value or depth > 4:
        return value or ''
    value = _TEXT_REF.sub(lambda m: _resolve_text(texts, texts.get((int(m[1]), int(m[2])), ''), depth + 1), value)
    value = re.sub(r'(?<!\\)\([^()]*\)', '', value)  # X4 drops unescaped (comments)
    return ' '.join(value.replace('\\(', '(').replace('\\)', ')').split())


def parse_catalog(wares_xml, text_xml=None):
    """Sort the game's wares into research, blueprint groups and tradeable wares."""
    texts = {}
    if text_xml:
        for page in ET.fromstring(text_xml).iter('page'):
            page_id = int(page.get('id', '0'))
            for entry in page.iter('t'):
                texts[(page_id, int(entry.get('id', '0')))] = entry.text or ''
    catalog = {'research': [], 'blueprints': {group: [] for group in BLUEPRINT_GROUPS}, 'wares': []}
    for ware in ET.fromstring(wares_xml).findall('ware'):
        ware_id = ware.get('id', '')
        if not _WARE.fullmatch(ware_id):
            continue
        tags = set((ware.get('tags') or '').split())
        transport = ware.get('transport')
        entry = (ware_id, _resolve_text(texts, ware.get('name')) or ware_id)
        if transport == 'research':
            if 'missiononly' not in tags:
                catalog['research'].append(entry)
            continue
        if 'economy' in tags:
            catalog['wares'].append(entry)
        if tags & _NO_BLUEPRINT:
            continue
        if 'module' in tags:
            catalog['blueprints']['modules'].append(entry)
        elif 'ship' in tags and transport == 'ship':
            catalog['blueprints']['ships'].append(entry)
        elif transport == 'equipment':
            catalog['blueprints']['equipment'].append(entry)
        elif 'equipmentmod' in tags:
            catalog['blueprints']['mods'].append(entry)
    for values in (catalog['research'], catalog['wares'], *catalog['blueprints'].values()):
        values.sort(key=lambda item: (item[1].lower(), item[0]))
    return catalog


def read_game_catalog(game, *, language=44):
    """Read the base game's ware list and English names; an empty catalogue on failure."""
    text_name = f't/0001-l{language:03d}.xml'
    try:
        files = _catalog_files(game, {'libraries/wares.xml', text_name})
        return parse_catalog(files['libraries/wares.xml'], files.get(text_name))
    except (OSError, KeyError, ValueError, ET.ParseError):
        return parse_catalog(b'<wares/>')


def relation_value(level):
    """Convert a UI relation level (-30 to 30) to X4's relation value (-1 to 1)."""
    if type(level) is not int or not -30 <= level <= 30:
        raise ValueError('relation level must be an integer from -30 to 30')
    if level == 0:
        return 0.0
    return math.copysign(min(1.0, 10 ** (abs(level) / 10) / 1000), level)


# --- X4 channel ------------------------------------------------------------

class HostAdminChannel:
    """A leased loopback UDP channel to the admin script inside the server's X4."""

    def __init__(self, *, timeout=20, clock=time.monotonic):
        if type(timeout) not in (int, float) or not 0 < timeout <= 120:
            raise ValueError('invalid host admin timeout')
        self._timeout = timeout
        self._clock = clock
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.bind(('127.0.0.1', 0))
            self._socket.setblocking(False)
        except BaseException:
            self._socket.close()
            raise
        self.lease = secrets.token_hex(16)
        self._peer = None
        self._active = None
        self._next_id = 1
        self._installed = False

    @property
    def port(self):
        return self._socket.getsockname()[1]

    @property
    def ready(self):
        return self._peer is not None

    @property
    def busy(self):
        return self._active is not None

    def install(self, case):
        """Stage the admin Lua and MD in the server's recovered world overlay."""
        if self._installed:
            raise ValueError('host admin is already staged')
        case = Path(case).absolute()
        extension = case / 'extensions/x4_lan_lab'
        if extension.resolve(strict=True) != extension or (
                hasattr(os, 'geteuid') and extension.stat().st_uid != os.geteuid()):
            raise ValueError('host admin requires an owned overlay')
        bridge = extension / 'ui/authority_world_bridge_probe.lua'
        manifest = extension / 'ui.xml'
        if any(path.is_symlink() or not path.is_file() for path in (bridge, manifest)):
            raise ValueError('host admin requires ordinary bridge overlay files')
        bridge_text = bridge.read_text(encoding='ascii')
        manifest_text = manifest.read_text(encoding='ascii')
        if (bridge_text.count(HOOK_TICK) != 1 or 'local registry_checkpoint' not in bridge_text
                or manifest_text.count(HOOK_ENTRY) != 1):
            raise ValueError('host admin requires the registry-aware recovered world bridge')
        source = Path(__file__).resolve().parent
        lua = extension / 'ui/authority_host_admin.lua'
        md = extension / 'md/authority_host_admin.xml'
        if any(path.exists() or path.is_symlink() for path in (lua, md)):
            raise FileExistsError('host admin scripts already present')
        md.parent.mkdir(exist_ok=True)
        for path in (lua, md):
            with path.open('xb') as output:
                output.write((source / path.name).read_bytes())
        hook = f""" if registry_checkpoint:ready() then
  if not host_admin_tick then
   local lib = assert(package.loadlib('./extensions/x4_lan_lab/native/x4lan_net.so','luaopen_x4lan_net'))()
   host_admin_tick = X4LAN_AUTHORITY_HOST_ADMIN.new("{self.lease}", assert(lib.open('127.0.0.1',{self.port})))
  end
  host_admin_tick(C.IsGamePaused())
 end
"""
        bridge.write_text('local host_admin_tick\n' + bridge_text.replace(HOOK_TICK, HOOK_TICK + hook),
                          encoding='ascii')
        manifest.write_text(manifest_text.replace(HOOK_ENTRY, '    <file name="ui/authority_host_admin.lua" />\n'
                                                  + HOOK_ENTRY), encoding='ascii')
        self._installed = True
        return [lua, md, bridge, manifest]

    def send(self, verb, *args):
        """Send one command; poll() later returns its rows."""
        if self._active is not None or self._peer is None:
            raise ValueError('host admin channel not ready')
        fields = [str(value) for value in (verb, *args)]
        if any(not field.isprintable() or '|' in field or not field.isascii() for field in fields):
            raise ValueError('invalid host admin command field')
        request_id = self._next_id
        packet = '|'.join(('ADMIN', self.lease, str(request_id), *fields)).encode('ascii')
        if len(packet) > _MAX_PACKET:
            raise ValueError('host admin command too long')
        self._next_id += 1
        self._active = {'id': request_id, 'verb': verb, 'rows': [], 'deadline': self._clock() + self._timeout}
        if self._socket.sendto(packet, self._peer) != len(packet):
            self._active = None
            raise OSError('short host admin send')
        return request_id

    def poll(self):
        """Return (id, verb, rows, error) once the active command ends, else None."""
        for _ in range(512):
            try:
                packet, source = self._socket.recvfrom(_MAX_PACKET + 1)
            except BlockingIOError:
                break
            except OSError:
                break  # e.g. a refused earlier send on some platforms
            if len(packet) > _MAX_PACKET:
                continue
            if packet == f'ADMIN_HELLO|{self.lease}'.encode('ascii'):
                if self._peer is None:
                    self._peer = source
                continue
            active = self._active
            if active is None or source != self._peer:
                continue
            marker = f'ADMIN_ROW|{self.lease}|{active["id"]}|'.encode('ascii')
            if not packet.startswith(marker):
                continue
            sequence, _, row = packet[len(marker):].partition(b'|')
            if sequence != str(len(active['rows']) + 1).encode('ascii'):
                return self._end('sequence_gap')
            row = row.decode('utf-8', errors='replace')
            if row == 'DONE':
                return self._end(None)
            if len(active['rows']) >= _MAX_ROWS:
                return self._end('row_limit')
            active['rows'].append(row)
        if self._active is not None and self._clock() >= self._active['deadline']:
            return self._end('timeout')
        return None

    def _end(self, error):
        active, self._active = self._active, None
        return active['id'], active['verb'], active['rows'], error

    def close(self):
        self._socket.close()


def parse_state_rows(rows):
    """Turn STATE rows into money, factions, ships and stations."""
    state = {'money_credits': None, 'factions': [], 'ships': [], 'stations': [], 'errors': []}
    for row in rows:
        kind, _, rest = row.partition('|')
        fields = rest.split('|')
        try:
            if kind == 'MONEY' and len(fields) == 1:
                state['money_credits'] = int(float(fields[0]))
            elif kind == 'FACTION' and len(fields) >= 3 and _FACTION.fullmatch(fields[0]):
                state['factions'].append({'id': fields[0], 'level': int(fields[1]), 'name': '|'.join(fields[2:])})
            elif kind == 'SHIP' and len(fields) >= 6 and _CODE.fullmatch(fields[1]):
                state['ships'].append({'ship': int(float(fields[0])), 'code': fields[1],
                                       'hull': round(float(fields[2]), 1), 'shield': round(float(fields[3]), 1),
                                       'sector': fields[4], 'name': '|'.join(fields[5:])})
            elif kind == 'STATION' and len(fields) >= 4 and _CODE.fullmatch(fields[0]):
                state['stations'].append({'station': fields[0], 'build_tasks': int(float(fields[1])),
                                          'sector': fields[2], 'name': '|'.join(fields[3:])})
            elif kind == 'ERROR':
                state['errors'].append(rest)
        except ValueError:
            state['errors'].append(f'unreadable {kind.lower()} row')
    state['factions'].sort(key=lambda item: item['name'].lower())
    state['ships'].sort(key=lambda item: item['ship'])
    state['stations'].sort(key=lambda item: item['name'].lower())
    return state


def _ware_batches(wares):
    batch = []
    for ware in wares:
        if batch and (len(batch) >= _MAX_WARES_PER_COMMAND or len(','.join(batch + [ware])) > 600):
            yield ','.join(batch)
            batch = []
        batch.append(ware)
    if batch:
        yield ','.join(batch)


# --- Actions -----------------------------------------------------------------

def _int(params, name, low, high):
    value = params.get(name)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer from {low} to {high}')
    return value


def _match(params, name, pattern):
    value = params.get(name)
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError(f'invalid {name}')
    return value


def validate_action(action, params, catalog):
    """Check one web request and return its normalised parameters."""
    if type(action) is not str or action not in _X4_ACTIONS | _SESSION_ACTIONS or type(params) is not dict:
        raise ValueError('unknown action')
    known = {ware for ware, _ in catalog['wares']}
    if action == 'money':
        credits = _int(params, 'credits', -1_000_000_000, 1_000_000_000)
        if credits == 0:
            raise ValueError('credits must not be zero')
        return {'credits': credits}
    if action == 'relation':
        return {'faction': _match(params, 'faction', _FACTION), 'level': _int(params, 'level', -30, 30)}
    if action in ('research', 'blueprints'):
        if action == 'research':
            choices = [ware for ware, _ in catalog['research']]
            group = 'all' if params.get('all') is True else None
        else:
            group = params.get('group')
            if group is not None and group not in BLUEPRINT_GROUPS:
                raise ValueError('unknown blueprint group')
            choices = [ware for ware, _ in catalog['blueprints'].get(group, ())] if group else []
        wares = params.get('wares')
        if group is not None:
            if wares is not None or not choices:
                raise ValueError('choose either a group or wares, from a loaded catalogue')
            wares = choices
        if (type(wares) is not list or not 1 <= len(wares) <= 2000
                or any(type(ware) is not str or not _WARE.fullmatch(ware) for ware in wares)):
            raise ValueError('wares must be a list of ware IDs')
        return {'wares': list(dict.fromkeys(wares)), **({'group': group} if group else {})}
    if action in ('ship_repair', 'ship_rename', 'ship_cargo'):
        result = {'ship': _int(params, 'ship', 1, 2**31 - 1), 'code': _match(params, 'code', _CODE)}
        if action == 'ship_rename':
            result['name'] = _match(params, 'name', _NAME).strip() or 'Ship'
        if action == 'ship_cargo':
            result['ware'] = _match(params, 'ware', _WARE)
            result['amount'] = _int(params, 'amount', 1, 1_000_000)
            if known and result['ware'] not in known:
                raise ValueError('unknown ware')
        return result
    if action in ('station_fill', 'station_add'):
        result = {'station': _match(params, 'station', _CODE)}
        if action == 'station_add':
            result['ware'] = _match(params, 'ware', _WARE)
            result['amount'] = _int(params, 'amount', 1, 1_000_000)
            if known and result['ware'] not in known:
                raise ValueError('unknown ware')
        return result
    if action == 'kick':
        return {'pilot': _int(params, 'pilot', 1, 2**53 - 1)}
    if action == 'hold':
        if type(params.get('hold')) is not bool:
            raise ValueError('hold must be true or false')
        return {'ship': _int(params, 'ship', 1, 2**31 - 1), 'hold': params['hold']}
    if action == 'assign':
        pilot = params.get('pilot')
        if pilot is not None:
            pilot = _int(params, 'pilot', 1, 2**53 - 1)
        return {'ship': _int(params, 'ship', 1, 2**31 - 1), 'pilot': pilot}
    return {}


def _commands(action, params):
    """Return the X4 commands for one action; a refresh always follows."""
    if action == 'money':
        return [('MONEY', params['credits'] * 100)]
    if action == 'relation':
        return [('RELATION', params['faction'], f'{relation_value(params["level"]):.6f}')]
    if action in ('research', 'blueprints'):
        verb = 'RESEARCH' if action == 'research' else 'BLUEPRINT'
        return [(verb, batch) for batch in _ware_batches(params['wares'])]
    if action == 'ship_repair':
        return [('REPAIR', params['ship'], params['code'])]
    if action == 'ship_rename':
        return [('RENAME', params['ship'], params['code'], params['name'])]
    if action == 'ship_cargo':
        return [('CARGO', params['ship'], params['code'], params['ware'], params['amount'])]
    if action == 'station_fill':
        return [('FILL', params['station'])]
    if action == 'station_add':
        return [('STORE', params['station'], params['ware'], params['amount'])]
    return []


class HostAdmin:
    """Run host menu jobs on the supervisor thread and publish a snapshot for the web page."""

    def __init__(self, *, control, session, lifecycle, channel, catalog, quiet, audit_path=None,
                 clock=time.monotonic, refresh_seconds=30, wait_seconds=60):
        if not callable(quiet):
            raise ValueError('quiet check must be callable')
        self._control = control
        self._session = session
        self._lifecycle = lifecycle
        self._channel = channel
        self.catalog = catalog
        self._quiet = quiet
        self._audit_path = None if audit_path is None else Path(audit_path)
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._wait_seconds = wait_seconds
        self._submitted = queue.Queue(maxsize=64)
        self._lock = threading.Lock()
        self._log = deque(maxlen=60)
        self._next_job = 1
        self._job = None
        self._next_refresh = 0.0
        self._next_view = 0.0
        self._researched = {}
        self._world = {'money_credits': None, 'factions': [], 'ships': [], 'stations': [], 'errors': [],
                       'refreshed': None}
        self._view = {'pilots': [], 'plan': [], 'world_state': None, 'x4_connected': False, 'busy': False}
        self.counts = {'jobs': 0, 'failed': 0}

    # Web thread ---------------------------------------------------------------
    def submit(self, action, params):
        params = validate_action(action, params, self.catalog)
        with self._lock:
            job = {'job': self._next_job, 'action': action, 'params': params, 'status': 'queued',
                   'detail': '', 'time': _now()}
            self._next_job += 1
            self._log.appendleft(job)
        try:
            self._submitted.put_nowait(job)
        except queue.Full:
            self._set(job, 'failed', 'too many queued jobs')
        return job['job']

    def snapshot(self):
        with self._lock:
            world = dict(self._world)
            world['research'] = dict(self._researched)
            jobs = [{key: job[key] for key in ('job', 'action', 'params', 'status', 'detail', 'time')}
                    for job in self._log]
            return {'world': world, **self._view, 'jobs': jobs}

    # Owner thread -------------------------------------------------------------
    def step(self):
        now = self._clock()
        if now >= self._next_view:
            self._next_view = now + 1
            self._publish_view()
        result = self._channel.poll() if self._channel is not None else None
        if result is not None and self._job is not None:
            self._command_done(*result)
        if self._job is None:
            self._start_next(now)
        if self._job is not None and self._job.get('commands') is not None:
            self._advance(now)

    def _start_next(self, now):
        try:
            job = self._submitted.get_nowait()
        except queue.Empty:
            if (self._channel is not None and self._channel.ready and now >= self._next_refresh
                    and self._quiet()):
                self._next_refresh = now + self._refresh_seconds
                self._job = {'job': None, 'action': 'refresh', 'commands': self._refresh_commands(),
                             'started': now, 'results': [], 'internal': True}
            return
        if job['action'] in _SESSION_ACTIONS:
            self._run_session_job(job)
            return
        if self._channel is None:
            self._finish(job, 'failed', 'this server runs without the X4 admin script')
            return
        commands = _commands(job['action'], job['params']) + self._refresh_commands()
        job.update(commands=commands, started=now, results=[])
        self._job = job
        self._set(job, 'running', 'waiting for a quiet world' if not self._quiet() else '')

    def _refresh_commands(self):
        commands = [('STATE',)]
        commands.extend(('RESEARCHED', batch) for batch in _ware_batches(
            [ware for ware, _ in self.catalog['research']]))
        return commands

    def _advance(self, now):
        job = self._job
        if self._channel.busy:
            return
        if not job['commands']:
            self._complete(job)
            return
        if not self._channel.ready or not self._quiet():
            if now - job['started'] >= self._wait_seconds:
                reason = 'X4 admin script not connected' if not self._channel.ready else 'world stayed busy'
                self._complete(job, failure=reason)
            return
        verb, *args = job['commands'][0]
        try:
            self._channel.send(verb, *args)
        except (ValueError, OSError) as error:
            self._complete(job, failure=f'send failed: {error}')
            return
        job['commands'].pop(0)

    def _command_done(self, request_id, verb, rows, error):
        job = self._job
        if error is not None:
            job['results'].append(f'{verb.lower()} {error}')
            if verb not in ('STATE', 'RESEARCHED'):
                self._complete(job, failure=f'{verb.lower()} {error}')
            return
        if verb == 'STATE':
            state = parse_state_rows(rows)
            state['refreshed'] = _now()
            with self._lock:
                self._world = state
            self._next_refresh = self._clock() + self._refresh_seconds
        elif verb == 'RESEARCHED':
            found = {}
            for row in rows:
                kind, ware, done = (row.split('|') + ['', '', ''])[:3]
                if kind == 'HAS' and _WARE.fullmatch(ware):
                    found[ware] = done == '1'
            with self._lock:
                self._researched.update(found)
        elif verb == 'MONEY':
            job['results'].extend(f'balance {row[6:]} Cr' for row in rows if row.startswith('MONEY|'))
            job['results'].extend(row for row in rows if row.startswith('ERROR|'))
        else:
            for row in rows:
                parts = row.split('|')
                if parts[0] == 'RESULT' and len(parts) >= 5:
                    job['results'].append(parts[3])  # RESULT|verb|target|status|detail
                elif parts[0] == 'ERROR':
                    job['results'].append('error ' + '|'.join(parts[1:]))

    def _complete(self, job, failure=None):
        self._job = None
        if job.get('internal'):
            return
        results = [item for item in job['results'] if item not in ('state', 'researched')]
        counted = {}
        for item in results:
            counted[item] = counted.get(item, 0) + 1
        detail = ', '.join(f'{name} x{count}' if count > 1 else name for name, count in counted.items())
        if failure is not None:
            self._finish(job, 'failed', failure + (f' ({detail})' if detail else ''))
        elif any(item.startswith(('refused', 'error', 'partial')) for item in results):
            ok = any(item in ('applied', 'unchanged') or item.startswith('balance') for item in results)
            self._finish(job, 'partial' if ok else 'failed', detail)
        else:
            self._finish(job, 'done', detail or 'ok')

    def _run_session_job(self, job):
        action, params = job['action'], job['params']
        try:
            if action == 'kick':
                self._control.kick(params['pilot'])
                detail = f'pilot {params["pilot"]} disconnected'
            elif action == 'hold':
                self._session.host_hold_ship(params['ship'], hold=params['hold'])
                detail = f'ship {params["ship"]} ' + ('held' if params['hold'] else 'released')
            elif action == 'assign':
                connection = None
                if params['pilot'] is not None:
                    connection = next((conn for conn, handle, _ in self._control.host_connections()
                                       if handle == params['pilot']), None)
                    if connection is None:
                        raise ValueError('pilot is not connected')
                self._session.host_assign_ship(params['ship'], connection)
                detail = (f'ship {params["ship"]} assigned to '
                          + ('the next pilot to connect' if connection is None else f'pilot {params["pilot"]}'))
            else:  # save
                if self._lifecycle.players:
                    raise ValueError('a save on demand needs an empty world')
                if not self._lifecycle.request_checkpoint():
                    raise ValueError('the world is not running')
                detail = 'checkpoint requested'
        except ValueError as error:
            self._finish(job, 'failed', str(error))
        else:
            self._finish(job, 'done', detail)
        self._publish_view()

    def _publish_view(self):
        connections = self._control.host_connections() if self._control is not None else []
        handles = {id(conn): handle for conn, handle, _ in connections}
        ready = {handle for handle, _ in self._lifecycle.ready_players}
        pilots = []
        for conn, handle, stage in connections:
            ship = self._session.host_pilot_ship(conn)
            pilots.append({'pilot': handle, 'stage': stage, 'ready': handle in ready,
                           'ship': None if ship is None else {'state': ship[0], 'ship': ship[1].ship_index,
                                                              'code': ship[1].code, 'name': ship[1].name}})
        plan = []
        for index, target in self._session.host_ship_plan().items():
            if target in ('held', 'next'):
                plan.append({'ship': index, 'target': target})
            else:
                plan.append({'ship': index, 'target': 'pilot', 'pilot': handles.get(id(target))})
        with self._lock:
            self._view = {'pilots': pilots, 'plan': plan, 'world_state': self._lifecycle.state,
                          'x4_connected': bool(self._channel is not None and self._channel.ready),
                          'busy': self._job is not None and not self._job.get('internal')}

    def _set(self, job, status, detail):
        with self._lock:
            job['status'], job['detail'] = status, detail

    def _finish(self, job, status, detail):
        self._set(job, status, detail)
        self.counts['jobs'] += 1
        if status == 'failed':
            self.counts['failed'] += 1
        if self._audit_path is not None:
            line = json.dumps({'time': _now(), 'job': job['job'], 'action': job['action'], 'params': job['params'],
                               'status': status, 'detail': detail}, sort_keys=True) + '\n'
            fd = os.open(self._audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode('utf-8'))
            finally:
                os.close(fd)


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# --- Web page ------------------------------------------------------------------

def load_or_create_token(path):
    """Return (token, created). A new token file is private to this user."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('host admin token must be a regular file') from None
        if hasattr(os, 'getuid') and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('host admin token must be owned by this user and private (chmod 600)') from None
        token = path.read_text(encoding='ascii').strip()
        if not _TOKEN.fullmatch(token):
            raise ValueError('host admin token file is malformed') from None
        return token, False
    token = secrets.token_urlsafe(24)
    try:
        os.write(fd, (token + '\n').encode('ascii'))
    finally:
        os.close(fd)
    return token, True


def _local(value):
    if not value:
        return False
    host = urlsplit(value if '//' in value else '//' + value).hostname
    return host in _LOCAL_HOSTS


class _Handler(BaseHTTPRequestHandler):
    server_version = 'X4HostAdmin'
    sys_version = ''

    def log_message(self, format, *args):  # keep the journal free of request lines
        pass

    def _send(self, code, body, content_type='application/json'):
        if not isinstance(body, bytes):
            body = json.dumps(body, separators=(',', ':')).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; "
                         "style-src 'unsafe-inline'; connect-src 'self'; img-src data:; base-uri 'none'; "
                         "form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self, *, api):
        # A local Host header blocks DNS rebinding; the token header blocks other sites.
        if not _local(self.headers.get('Host')):
            self._send(403, {'error': 'use a localhost address'})
            return False
        origin = self.headers.get('Origin')
        if origin is not None and not _local(origin):
            self._send(403, {'error': 'cross-site request refused'})
            return False
        if api and not hmac.compare_digest(self.headers.get('X-Host-Admin-Token', '').encode('utf-8'),
                                           self.server.token.encode('ascii')):
            self._send(401, {'error': 'host token required'})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ('/', '/index.html'):
            if self._allowed(api=False):
                self._send(200, self.server.page, 'text/html')
        elif path == '/api/state':
            if self._allowed(api=True):
                self._send(200, self.server.admin.snapshot())
        elif path == '/api/catalog':
            if self._allowed(api=True):
                self._send(200, self.server.admin.catalog)
        else:
            self._send(404, {'error': 'not found'})

    def do_POST(self):
        if urlsplit(self.path).path != '/api/action':
            self._send(404, {'error': 'not found'})
            return
        if not self._allowed(api=True):
            return
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            length = -1
        if not 0 < length <= _MAX_BODY or self.headers.get_content_type() != 'application/json':
            self._send(400, {'error': 'send a JSON body of at most 16 KiB'})
            return
        try:
            body = json.loads(self.rfile.read(length))
            if type(body) is not dict:
                raise ValueError('JSON object required')
            job = self.server.admin.submit(body.get('action'), body.get('params', {}))
        except ValueError as error:
            self._send(400, {'error': str(error)})
            return
        self._send(202, {'job': job})

    def _refuse(self):
        self._send(405, {'error': 'method not allowed'})

    do_PUT = do_DELETE = do_PATCH = _refuse


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8


class HostAdminWeb:
    """Serve the host menu on 127.0.0.1 in a background thread."""

    def __init__(self, admin, token, *, port, page=None):
        if type(port) is not int or not (port == 0 or 1024 <= port <= 65535):
            raise ValueError('host admin port must be 0 or 1024-65535')
        if not _TOKEN.fullmatch(token):
            raise ValueError('invalid host admin token')
        if page is None:
            page = (Path(__file__).resolve().parent / 'authority_host_admin.html').read_bytes()
        self._server = _Server(('127.0.0.1', port), _Handler)
        self._server.admin = admin
        self._server.token = token
        self._server.page = page
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': .5},
                                        name='host-admin-web', daemon=True)

    @property
    def port(self):
        return self._server.server_address[1]

    def start(self):
        self._thread.start()
        return self

    def close(self):
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(5)
        self._server.server_close()
