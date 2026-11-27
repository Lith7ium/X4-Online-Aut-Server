"""Host admin menu: catalogue, X4 channel, job runner and web page; no X4."""
from collections import deque
import http.client
import json
from pathlib import Path
import re
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools/dev'), str(ROOT / 'src')]
import authority_host_admin as admin  # noqa: E402

WARES = b'''<wares>
  <ware id="research_alpha" transport="research" tags="research" name="{900,1}"/>
  <ware id="research_hidden" transport="research" tags="missiononly research" name="Hidden"/>
  <ware id="module_x" transport="container" tags="module" name="{900,2}"/>
  <ware id="module_npc" transport="container" tags="module noplayerblueprint" name="NPC module"/>
  <ware id="ship_x" transport="ship" tags="ship" name="Scout"/>
  <ware id="engine_x" transport="equipment" tags="engine equipment" group="engines" name="Engine"/>
  <ware id="mod_x" transport="inventory" tags="crafting equipmentmod" name="Mod"/>
  <ware id="energycells" transport="container" tags="container economy" name="{900,3}"/>
</wares>'''
TEXT = b'''<language id="44"><page id="900">
  <t id="1">(do not show)Teleportation</t><t id="2">{900,4} Dock</t><t id="3">Energy Cells</t><t id="4">Basic</t>
</page></language>'''
CATALOG = admin.parse_catalog(WARES, TEXT)
TOKEN = 'token-token-token-token-token'
CONN1, CONN2 = object(), object()


class CatalogTests(unittest.TestCase):
    def test_wares_are_sorted_into_research_blueprints_and_trade_wares(self):
        self.assertEqual(CATALOG['research'], [('research_alpha', 'Teleportation')])
        self.assertEqual(CATALOG['blueprints'], {'modules': [('module_x', 'Basic Dock')], 'ships': [('ship_x', 'Scout')],
                                                 'equipment': [('engine_x', 'Engine')], 'mods': [('mod_x', 'Mod')]})
        self.assertEqual(CATALOG['wares'], [('energycells', 'Energy Cells')])

    def test_reads_the_last_catalogue_entry_and_survives_a_missing_game(self):
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            old = b'<wares/>'
            (game / '01.cat').write_text(f'libraries/wares.xml {len(old)} 0 x\n', encoding='utf-8')
            (game / '01.dat').write_bytes(old)
            other = b'abc'
            (game / '02.cat').write_text(f'other file.txt {len(other)} 0 x\nlibraries/wares.xml {len(WARES)} 0 x\n'
                                         f't/0001-l044.xml {len(TEXT)} 0 x\n', encoding='utf-8')
            (game / '02.dat').write_bytes(other + WARES + TEXT)
            self.assertEqual(admin.read_game_catalog(game), CATALOG)
            empty = admin.read_game_catalog(game / 'missing')
        self.assertEqual(empty['research'], [])

    def test_relation_levels_follow_the_game_scale(self):
        self.assertEqual(admin.relation_value(0), 0.0)
        self.assertEqual(admin.relation_value(30), 1.0)
        self.assertAlmostEqual(admin.relation_value(-25), -0.3162, places=4)
        self.assertAlmostEqual(admin.relation_value(10), 0.01)
        for bad in (31, -31, 1.5, True):
            with self.assertRaises(ValueError):
                admin.relation_value(bad)


class ValidationTests(unittest.TestCase):
    def check(self, action, params):
        return admin.validate_action(action, params, CATALOG)

    def test_valid_actions_are_normalised(self):
        self.assertEqual(self.check('money', {'credits': -5}), {'credits': -5})
        self.assertEqual(self.check('research', {'all': True}), {'wares': ['research_alpha'], 'group': 'all'})
        self.assertEqual(self.check('blueprints', {'group': 'ships'}), {'wares': ['ship_x'], 'group': 'ships'})
        self.assertEqual(self.check('blueprints', {'wares': ['a', 'a', 'b']}), {'wares': ['a', 'b']})
        self.assertEqual(self.check('ship_rename', {'ship': 3, 'code': 'ABC-123', 'name': ' Vale '}),
                         {'ship': 3, 'code': 'ABC-123', 'name': 'Vale'})
        self.assertEqual(self.check('assign', {'ship': 3}), {'ship': 3, 'pilot': None})
        self.assertEqual(self.check('station_add', {'station': 'XYZ-001', 'ware': 'energycells', 'amount': 5}),
                         {'station': 'XYZ-001', 'ware': 'energycells', 'amount': 5})

    def test_invalid_actions_are_refused(self):
        cases = [('shutdown', {}), ('money', {'credits': 0}), ('money', {'credits': 10**10}),
                 ('money', {'credits': '5'}), ('relation', {'faction': 'Argon', 'level': 1}),
                 ('relation', {'faction': 'argon', 'level': 40}), ('blueprints', {'group': 'all'}),
                 ('blueprints', {'group': 'ships', 'wares': ['x']}), ('research', {'wares': []}),
                 ('ship_rename', {'ship': 3, 'code': 'ABC-123', 'name': 'a|b'}),
                 ('ship_rename', {'ship': 3, 'code': 'abc-123', 'name': 'ok'}),
                 ('ship_cargo', {'ship': 3, 'code': 'ABC-123', 'ware': 'gold', 'amount': 5}),
                 ('station_add', {'station': 'XYZ-001', 'ware': 'energycells', 'amount': 0}),
                 ('hold', {'ship': 3, 'hold': 1}), ('kick', {'pilot': True}), ('money', [])]
        for action, params in cases:
            with self.subTest(action=action, params=params), self.assertRaises(ValueError):
                self.check(action, params)

    def test_ware_batches_fit_the_native_packet(self):
        wares = [f'research_{index:03d}_' + 'x' * 30 for index in range(100)]
        commands = admin._commands('research', {'wares': wares})
        self.assertEqual(sum(len(batch.split(',')) for _, batch in commands), 100)
        for _, batch in commands:
            packet = '|'.join(('ADMIN', 'f' * 32, '999999999', 'RESEARCH', batch))
            self.assertLessEqual(len(packet), 768)
            self.assertLessEqual(len(batch.split(',')), 40)
        self.assertEqual(admin._commands('money', {'credits': -3}), [('MONEY', -300)])
        self.assertEqual(admin._commands('relation', {'faction': 'argon', 'level': 30}),
                         [('RELATION', 'argon', '1.000000')])


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.channel = admin.HostAdminChannel(timeout=5, clock=lambda: self.now[0])
        self.addCleanup(self.channel.close)
        self.lua = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.lua.close)
        self.lua.bind(('127.0.0.1', 0))
        self.lua.settimeout(1)
        self.address = ('127.0.0.1', self.channel.port)

    def hello(self):
        self.lua.sendto(f'ADMIN_HELLO|{self.channel.lease}'.encode(), self.address)
        for _ in range(50):
            self.channel.poll()
            if self.channel.ready:
                return
        self.fail('hello not received')

    def receive_command(self):
        data, _ = self.lua.recvfrom(1024)
        return data.decode().split('|')

    def reply(self, request_id, rows):
        for sequence, row in enumerate(rows, 1):
            self.lua.sendto(f'ADMIN_ROW|{self.channel.lease}|{request_id}|{sequence}|{row}'.encode(), self.address)

    def finish(self):
        for _ in range(100):
            result = self.channel.poll()
            if result is not None:
                return result
        self.fail('command did not end')

    def test_command_rows_end_with_done_and_foreign_rows_are_ignored(self):
        with self.assertRaises(ValueError):
            self.channel.send('STATE')
        self.hello()
        request_id = self.channel.send('MONEY', 500)
        fields = self.receive_command()
        self.assertEqual(fields, ['ADMIN', self.channel.lease, str(request_id), 'MONEY', '500'])
        self.lua.sendto(f'ADMIN_ROW|{"0" * 32}|{request_id}|1|MONEY|9'.encode(), self.address)
        self.reply(request_id, ['MONEY|1500', 'DONE'])
        self.assertEqual(self.finish(), (request_id, 'MONEY', ['MONEY|1500'], None))
        self.assertFalse(self.channel.busy)

    def test_sequence_gap_timeout_and_unsafe_fields(self):
        self.hello()
        request_id = self.channel.send('STATE')
        self.receive_command()
        self.lua.sendto(f'ADMIN_ROW|{self.channel.lease}|{request_id}|2|MONEY|1'.encode(), self.address)
        self.assertEqual(self.finish()[3], 'sequence_gap')
        self.channel.send('STATE')
        self.now[0] = 6
        self.assertEqual(self.finish()[3], 'timeout')
        for field in ('a|b', 'line\n', 'café', 'x' * 800):
            with self.assertRaises(ValueError):
                self.channel.send('RENAME', 1, 'ABC-123', field)

    def test_install_stages_scripts_and_keeps_the_bridge_hook_points(self):
        with tempfile.TemporaryDirectory() as temp:
            extension = Path(temp, 'case/extensions/x4_lan_lab')
            (extension / 'ui').mkdir(parents=True)
            bridge = extension / 'ui/authority_world_bridge_probe.lua'
            bridge.write_text('local registry_checkpoint\n' + admin.HOOK_TICK, encoding='ascii')
            (extension / 'ui.xml').write_text('<files>\n' + admin.HOOK_ENTRY + '</files>\n', encoding='ascii')
            staged = self.channel.install(extension.parents[1])
            self.assertEqual([path.name for path in staged], ['authority_host_admin.lua', 'authority_host_admin.xml',
                                                               'authority_world_bridge_probe.lua', 'ui.xml'])
            text = bridge.read_text(encoding='ascii')
            manifest = (extension / 'ui.xml').read_text(encoding='ascii')
            self.assertEqual(text.count(admin.HOOK_TICK), 1)
            self.assertIn(f'X4LAN_AUTHORITY_HOST_ADMIN.new("{self.channel.lease}"', text)
            self.assertIn(f"lib.open('127.0.0.1',{self.channel.port})", text)
            self.assertEqual(manifest.count(admin.HOOK_ENTRY), 1)
            self.assertLess(manifest.index('authority_host_admin.lua'), manifest.index(admin.HOOK_ENTRY))
            with self.assertRaises(ValueError):
                self.channel.install(extension.parents[1])


class ScriptConsistencyTests(unittest.TestCase):
    def test_md_and_lua_agree_on_screen_events_and_verbs(self):
        lua = (ROOT / 'tools/dev/authority_host_admin.lua').read_text(encoding='utf-8')
        md = ET.parse(ROOT / 'tools/dev/authority_host_admin.xml').getroot()
        self.assertEqual(md.get('name'), 'X4AuthorityHostAdmin')
        controls = {node.get('control').strip("'") for node in md.iter('event_ui_triggered')}
        self.assertEqual(controls, {'Snapshot', 'Apply'})
        for control in controls:
            self.assertIn(f"trigger('{control}'", lua)
        events = {node.get('name').strip("'") for node in md.iter('raise_lua_event')}
        self.assertEqual(events, {'X4AuthorityHostAdmin.Row', 'X4AuthorityHostAdmin.Done'})
        self.assertIn("RegisterEvent(screen .. '.Row'", lua)
        self.assertIn("RegisterEvent(screen .. '.Done'", lua)
        lua_verbs = set(re.findall(r'(\w+)=\d', re.search(r'local md_verbs = \{([^}]*)\}', lua)[1]))
        md_text = (ROOT / 'tools/dev/authority_host_admin.xml').read_text(encoding='utf-8')
        md_verbs = set(re.findall(r"\$verb == '(\w+)'", md_text))
        self.assertEqual(lua_verbs, md_verbs)
        python_verbs = {command[0] for action in admin._X4_ACTIONS - {'refresh'}
                        for command in admin._commands(action, {
                            'credits': 1, 'faction': 'argon', 'level': 1, 'wares': ['a'], 'ship': 1,
                            'code': 'ABC-123', 'name': 'n', 'ware': 'a', 'amount': 1, 'station': 'ABC-123'})}
        self.assertEqual(python_verbs - {'MONEY'}, lua_verbs)


class FakeChannel:
    ready = True

    def __init__(self):
        self.sent = []
        self.busy = False
        self.errors = {}

    def send(self, verb, *args):
        self.sent.append((verb, *args))
        self.busy = True
        return len(self.sent)

    def poll(self):
        if not self.busy:
            return None
        self.busy = False
        verb, *args = self.sent[-1]
        rows = {'STATE': ['MONEY|1500', 'FACTION|argon|5|Argon Federation', 'SHIP|1|ABC-123|100|50|Argon Prime|Scout'],
                'RESEARCHED': ['HAS|research_alpha|1'], 'MONEY': ['MONEY|1500']}.get(verb)
        if rows is None:
            rows = [f'RESULT|{verb}|{item}|applied|-' for item in str(args[0]).split(',')]
        return len(self.sent), verb, rows, self.errors.get(verb)


class FakeControl:
    def __init__(self):
        self.kicked = []

    def host_connections(self):
        return [(CONN1, 1, 'admitted'), (CONN2, 2, 'offered')]

    def kick(self, handle):
        if handle != 1:
            raise ValueError('unknown pilot connection')
        self.kicked.append(handle)


class FakeSession:
    def __init__(self):
        self.calls = []

    def host_ship_plan(self):
        return {4: 'held', 5: CONN2}

    def host_pilot_ship(self, conn):
        ship = SimpleNamespace(ship_index=1, code='ABC-123', name='Scout')
        return ('flying', ship) if conn is CONN1 else None

    def host_hold_ship(self, index, hold=True):
        self.calls.append(('hold', index, hold))

    def host_assign_ship(self, index, conn=None):
        self.calls.append(('assign', index, conn))


class HostAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [0.0]
        self.quiet = [True]
        self.channel = FakeChannel()
        self.control = FakeControl()
        self.session = FakeSession()
        self.lifecycle = SimpleNamespace(state='running', players=((1, 'a'),), ready_players=((1, 'a'),),
                                         request_checkpoint=lambda: True)
        self.audit = Path(self.temp.name, 'audit.jsonl')
        self.admin = admin.HostAdmin(control=self.control, session=self.session, lifecycle=self.lifecycle,
                                     channel=self.channel, catalog=CATALOG, quiet=lambda: self.quiet[0],
                                     audit_path=self.audit, clock=lambda: self.now[0], refresh_seconds=30,
                                     wait_seconds=60)

    def run_steps(self, count=20):
        for _ in range(count):
            self.admin.step()

    def job(self, number):
        return next(job for job in self.admin.snapshot()['jobs'] if job['job'] == number)

    def test_world_edit_runs_then_refreshes_the_snapshot(self):
        self.admin._next_refresh = 10**9  # no background refresh in this test
        number = self.admin.submit('research', {'all': True})
        self.run_steps()
        self.assertEqual(self.channel.sent, [('RESEARCH', 'research_alpha'), ('STATE',), ('RESEARCHED', 'research_alpha')])
        self.assertEqual(self.job(number)['status'], 'done')
        self.assertEqual(self.job(number)['detail'], 'applied')
        snapshot = self.admin.snapshot()
        self.assertEqual(snapshot['world']['money_credits'], 1500)
        self.assertEqual(snapshot['world']['research'], {'research_alpha': True})
        self.assertEqual(snapshot['world']['ships'][0]['code'], 'ABC-123')
        self.assertEqual(snapshot['pilots'][0]['ship']['state'], 'flying')
        self.assertEqual(snapshot['plan'], [{'ship': 4, 'target': 'held'}, {'ship': 5, 'target': 'pilot', 'pilot': 2}])
        line = json.loads(self.audit.read_text(encoding='utf-8'))
        self.assertEqual((line['action'], line['status']), ('research', 'done'))
        self.assertNotIn('commands', snapshot['jobs'][0])

    def test_edits_wait_for_a_quiet_world_and_give_up(self):
        self.quiet[0] = False
        number = self.admin.submit('money', {'credits': 10})
        self.run_steps()
        self.assertEqual(self.channel.sent, [])
        self.assertEqual(self.job(number)['status'], 'running')
        self.now[0] = 61
        self.run_steps()
        self.assertEqual(self.job(number)['status'], 'failed')
        self.assertIn('world stayed busy', self.job(number)['detail'])

    def test_native_error_fails_the_job(self):
        self.admin._next_refresh = 10**9
        self.channel.errors['MONEY'] = 'timeout'
        number = self.admin.submit('money', {'credits': 10})
        self.run_steps()
        self.assertEqual(self.job(number)['status'], 'failed')
        self.assertEqual(self.channel.sent, [('MONEY', 1000)])

    def test_background_refresh_runs_only_when_quiet(self):
        self.quiet[0] = False
        self.run_steps(3)
        self.assertEqual(self.channel.sent, [])
        self.quiet[0] = True
        self.run_steps(5)
        self.assertEqual(self.channel.sent[0], ('STATE',))
        count = len(self.channel.sent)
        self.run_steps(5)
        self.assertEqual(len(self.channel.sent), count)
        self.assertEqual(self.admin.snapshot()['jobs'], [])

    def test_session_actions(self):
        kick = self.admin.submit('kick', {'pilot': 1})
        missing = self.admin.submit('kick', {'pilot': 9})
        hold = self.admin.submit('hold', {'ship': 4, 'hold': False})
        assign = self.admin.submit('assign', {'ship': 6, 'pilot': 2})
        lost = self.admin.submit('assign', {'ship': 6, 'pilot': 7})
        save = self.admin.submit('save', {})
        self.run_steps(10)
        self.assertEqual(self.control.kicked, [1])
        self.assertEqual(self.session.calls, [('hold', 4, False), ('assign', 6, CONN2)])
        self.assertEqual([self.job(n)['status'] for n in (kick, missing, hold, assign, lost, save)],
                         ['done', 'failed', 'done', 'done', 'failed', 'failed'])
        self.assertIn('empty world', self.job(save)['detail'])
        self.lifecycle.players = ()
        save = self.admin.submit('save', {})
        self.run_steps(2)
        self.assertEqual(self.job(save)['status'], 'done')


class TokenTests(unittest.TestCase):
    def test_token_is_created_once_and_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, 'config/host-admin.token')
            token, created = admin.load_or_create_token(path)
            self.assertTrue(created)
            self.assertEqual(admin.load_or_create_token(path), (token, False))
            path.write_text('short\n', encoding='ascii')
            with self.assertRaises(ValueError):
                admin.load_or_create_token(path)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.submitted = []
        outer = self

        class Admin:
            catalog = CATALOG

            def snapshot(self):
                return {'world': {}, 'jobs': []}

            def submit(self, action, params):
                admin.validate_action(action, params, CATALOG)
                outer.submitted.append((action, params))
                return 7

        self.web = admin.HostAdminWeb(Admin(), TOKEN, port=0, page=b'<p>menu</p>').start()
        self.addCleanup(self.web.close)

    def request(self, method, path, body=None, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.web.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.read(), response
        finally:
            connection.close()

    def test_page_is_public_but_the_api_needs_the_token(self):
        status, body, response = self.request('GET', '/')
        self.assertEqual((status, body), (200, b'<p>menu</p>'))
        self.assertIn("frame-ancestors 'none'", response.getheader('Content-Security-Policy'))
        self.assertEqual(self.request('GET', '/api/state')[0], 401)
        self.assertEqual(self.request('GET', '/api/state', **{'X-Host-Admin-Token': 'wrong'})[0], 401)
        status, body, _ = self.request('GET', '/api/catalog', **{'X-Host-Admin-Token': TOKEN})
        self.assertEqual((status, json.loads(body)['research']), (200, [['research_alpha', 'Teleportation']]))

    def test_foreign_host_and_origin_are_refused(self):
        self.assertEqual(self.request('GET', '/', Host='attacker.example')[0], 403)
        self.assertEqual(self.request('GET', '/api/state', Host=f'localhost:9000',
                                      **{'X-Host-Admin-Token': TOKEN})[0], 200)
        body = json.dumps({'action': 'money', 'params': {'credits': 5}})
        self.assertEqual(self.request('POST', '/api/action', body, Origin='https://attacker.example',
                                      **{'Content-Type': 'application/json', 'X-Host-Admin-Token': TOKEN})[0], 403)
        self.assertEqual(self.submitted, [])

    def test_port_is_bound_before_the_admin_is_attached(self):
        early = admin.HostAdminWeb(None, TOKEN, port=0, page=b'')
        self.addCleanup(early.close)
        with self.assertRaises(OSError):
            admin.HostAdminWeb(None, TOKEN, port=early.port, page=b'')
        with self.assertRaises(ValueError):
            early.start()
        early.attach(SimpleNamespace(catalog=CATALOG))
        with self.assertRaises(ValueError):
            early.attach(SimpleNamespace())
        early.start()
        self.assertEqual(self.request_to(early.port, '/api/catalog')[0], 200)

    def request_to(self, port, path):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            connection.request('GET', path, headers={'X-Host-Admin-Token': TOKEN})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_actions_are_queued_or_refused(self):
        headers = {'Content-Type': 'application/json', 'X-Host-Admin-Token': TOKEN}
        body = json.dumps({'action': 'money', 'params': {'credits': 5}})
        status, reply, _ = self.request('POST', '/api/action', body, **headers)
        self.assertEqual((status, json.loads(reply)), (202, {'job': 7}))
        self.assertEqual(self.submitted, [('money', {'credits': 5})])
        status, reply, _ = self.request('POST', '/api/action', json.dumps({'action': 'money', 'params': {}}), **headers)
        self.assertEqual(status, 400)
        self.assertIn('credits', json.loads(reply)['error'])
        self.assertEqual(self.request('POST', '/api/action', body, **{**headers, 'Content-Type': 'text/plain'})[0], 400)
        self.assertEqual(self.request('POST', '/api/action', '[1]', **headers)[0], 400)
        self.assertEqual(self.request('DELETE', '/api/action', **headers)[0], 405)


if __name__ == '__main__':
    unittest.main()
