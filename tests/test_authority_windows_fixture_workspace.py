"""Filesystem-backed workspace checks with Windows APIs and game setup mocked."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from authority import windows_fixture_workspace as subject

_CREATE_VIEW = subject.game.create_view


class WindowsFixtureWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = self.root / 'game'
        self.game.mkdir()
        (self.game / 'X4.exe').write_bytes(b'pinned fixture executable')
        (self.game / 'extensions' / 'ego_dlc_1').mkdir(parents=True)
        self.documents = self.root / 'documents' / 'Egosoft' / 'X4'
        self.documents.mkdir(parents=True)
        self.view_container = self.root / 'private-view'
        self.run_directory = self.root / 'private-run'
        self.commands = []
        self.acl_targets = []
        self.workspace = subject.WindowsFixtureWorkspace(
            self.game, self.view_container, self.run_directory)
        self.patches = [
            patch.object(subject, '_is_windows', return_value=True),
            patch.object(subject.profile, 'documents', return_value=self.documents),
            patch.object(subject.game, 'verify_exe'),
            patch.object(subject.game, 'create_view', side_effect=self._create_view),
            patch.object(subject.subprocess, 'run', side_effect=self._command),
        ]
        for active_patch in self.patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

    def _command(self, command, **options):
        self.commands.append(command)
        if command[0] == 'icacls':
            target = Path(command[1])
            self.acl_targets.append(target)
            if target == self.view_container:
                self.assertFalse((self.view_container / 'game').exists())
        if command[0] == 'tasklist':
            output = '"Steam.exe","100"\r\n'
        elif command[0] == 'whoami':
            output = '"USER","S-1-5-21-1-2-3-1000"\r\n'
        else:
            output = ''
        return SimpleNamespace(stdout=output, stderr='', returncode=0)

    def _create_view(self, game_directory, view):
        view.mkdir()
        (view / '.x4ls-view').write_text(str(game_directory) + '\n')
        for entry in Path(game_directory).iterdir():
            if entry.name == 'extensions':
                continue
            if entry.is_dir():
                (view / entry.name).mkdir()
            else:
                os.link(entry, view / entry.name)
        extensions = view / 'extensions'
        extensions.mkdir()
        for dlc in (Path(game_directory) / 'extensions').glob('ego_dlc_*'):
            (extensions / dlc.name).mkdir()

    def test_base_game_view_omits_owned_dlc_without_touching_install(self):
        workspace = subject.WindowsFixtureWorkspace(self.game, self.view_container,
                                                    self.run_directory, base_game_only=True)

        def create_view(game, view, **options):
            return _CREATE_VIEW(game, view, junction=lambda source, target: target.mkdir(),
                                **options)

        with patch.object(subject.game, 'create_view', side_effect=create_view):
            workspace.prepare()
        self.assertEqual(list((workspace.view / 'extensions').iterdir()), [])
        self.assertEqual((workspace.profile_directory / 'content.xml').read_text(),
                         subject.profile.content_xml(['x4_lan_lab']))
        self.assertTrue((self.game / 'extensions' / 'ego_dlc_1').is_dir())

    def _install_overlay_files(self):
        extension = self.workspace.view / 'extensions' / 'x4_lan_lab'
        files = {
            'ui/config.lua': b'private ticket config',
            'ui/probe.lua': b'fixture probe',
            'native/x4lan_net_64.dll': b'MZ fixture dll',
        }
        for relative, data in files.items():
            path = extension / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}

    def _prepare_and_register(self):
        self.workspace.prepare()
        self.assertFalse(self.workspace.download_dir.exists())
        # The receive layer creates this child beneath the restricted run root.
        self.workspace.download_dir.mkdir()
        (self.workspace.save_dir / 'save_009.xml.gz').write_bytes(b'staged fixture save')
        self.workspace.register_overlay(self._install_overlay_files())
        return self.workspace

    def test_prepare_register_log_append_redact_and_close_retain_artifacts(self):
        workspace = self._prepare_and_register()
        settings = workspace.profile_directory / 'config.xml'
        settings.write_bytes(b'X4 rewrote harmless runtime settings')
        self.assertTrue(workspace.guard())
        self.assertTrue(any(command[0] == 'icacls' and str(self.run_directory) in command
                            for command in self.commands))
        self.assertTrue(any(command[0] == 'icacls' and str(self.view_container) in command
                            for command in self.commands))
        self.assertEqual(self.acl_targets, [
            self.view_container,
            self.run_directory,
            self.documents / '45841',
            self.documents / '45841' / 'save',
            self.documents / '45841' / 'config.xml',
            self.documents / '45841' / 'content.xml',
        ])
        self.assertNotIn(self.game, self.acl_targets)
        self.assertNotIn(workspace.view, self.acl_targets)

        self.assertEqual(workspace.read_log(), b'')
        workspace.log_path.write_bytes(b'first line\n')
        first = workspace.read_log()
        self.assertEqual(first, b'first line\n')
        self.assertIs(workspace.read_log(), first)
        with workspace.log_path.open('ab') as output:
            output.write(b'second line\n')
        self.assertEqual(workspace.read_log(), b'first line\nsecond line\n')
        self.assertTrue(workspace.guard())

        config = workspace.view / 'extensions' / 'x4_lan_lab' / 'ui' / 'config.lua'
        self.assertTrue(workspace.redact_config())
        self.assertFalse(config.exists())
        self.assertTrue((config.parent / 'probe.lua').exists())
        workspace.close()
        self.assertTrue(workspace.view.exists())
        self.assertTrue(workspace.save_dir.exists())
        self.assertTrue(workspace.run_directory.exists())

    def test_guard_rejects_unmanifested_fixture_script(self):
        workspace = self._prepare_and_register()
        extra = workspace.view / 'extensions' / 'x4_lan_lab' / 'md' / 'unexpected.xml'
        extra.parent.mkdir()
        extra.write_text('<md/>')
        with self.assertRaisesRegex(ValueError, 'tree differs'):
            workspace.guard()

    def test_manifested_nested_faction_asset_is_pinned_by_guard(self):
        self.workspace.prepare()
        (self.workspace.save_dir / 'save_009.xml.gz').write_bytes(b'staged fixture save')
        hashes = self._install_overlay_files()
        asset = self.workspace.view / 'extensions/x4_lan_lab/assets/textures/ui/x4lan/x4lan_player1.dds'
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b'fixture texture')
        hashes[asset.relative_to(self.workspace.view / 'extensions/x4_lan_lab').as_posix()] = hashlib.sha256(asset.read_bytes()).hexdigest()
        self.workspace.register_overlay(hashes)
        self.assertTrue(self.workspace.guard())
        asset.write_bytes(b'changed texture')
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            self.workspace.guard()

    def test_register_does_not_bless_changed_existing_view_entries(self):
        self.workspace.prepare()
        marker = self.workspace.view / '.x4ls-view'
        marker.write_text('changed view marker\n')
        (self.workspace.save_dir / 'save_009.xml.gz').write_bytes(b'staged fixture save')
        hashes = self._install_overlay_files()
        with self.assertRaisesRegex(ValueError, 'pre-existing game view entry changed'):
            self.workspace.register_overlay(hashes)

    def test_register_rejects_prelaunch_settings_change(self):
        self.workspace.prepare()
        (self.workspace.save_dir / 'save_009.xml.gz').write_bytes(b'staged fixture save')
        hashes = self._install_overlay_files()
        content = self.workspace.profile_directory / 'content.xml'
        content.write_bytes(b'changed before launch')
        with self.assertRaisesRegex(ValueError, 'settings changed before launch'):
            self.workspace.register_overlay(hashes)

    def test_log_replacement_faults_the_reader(self):
        workspace = self._prepare_and_register()
        workspace.log_path.write_bytes(b'original\n')
        workspace.read_log()
        replacement = workspace.log_path.with_name('replacement.log')
        replacement.write_bytes(b'other file\n')
        os.replace(replacement, workspace.log_path)
        with self.assertRaisesRegex(RuntimeError, 'replaced, shrunk'):
            workspace.read_log()

    def test_log_truncation_faults_the_reader(self):
        workspace = self._prepare_and_register()
        workspace.log_path.write_bytes(b'original line\n')
        workspace.read_log()
        workspace.log_path.write_bytes(b'short\n')
        with self.assertRaisesRegex(RuntimeError, 'replaced, shrunk'):
            workspace.read_log()

    def test_running_x4_is_refused_before_any_workspace_mutation(self):
        workspace = subject.WindowsFixtureWorkspace(
            self.game, self.view_container, self.run_directory)
        with patch.object(subject.subprocess, 'run', return_value=SimpleNamespace(
                stdout='"X4.exe","42"\r\n', stderr='', returncode=0)):
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                workspace.prepare()
        self.assertFalse(self.view_container.exists())
        self.assertFalse(self.run_directory.exists())
        self.assertFalse((self.documents / '45841').exists())

    def test_existing_fixture_profile_and_sentinel_are_preserved(self):
        profile = self.documents / '45841'
        profile.mkdir()
        sentinel = profile / 'keep.bin'
        sentinel.write_bytes(b'keep this existing profile')
        with self.assertRaises(FileExistsError):
            self.workspace.prepare()
        self.assertEqual(sentinel.read_bytes(), b'keep this existing profile')
        self.assertFalse(self.view_container.exists())
        self.assertFalse(self.run_directory.exists())
        self.assertEqual(self.acl_targets, [])

    def test_prepare_requires_windows_before_mutating(self):
        with patch.object(subject, '_is_windows', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'requires Windows'):
                self.workspace.prepare()
        self.assertFalse(self.view_container.exists())
        self.assertFalse(self.run_directory.exists())
        self.assertFalse((self.documents / '45841').exists())


if __name__ == '__main__':
    unittest.main()
