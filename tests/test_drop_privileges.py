import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from drop_privileges import main


class DropPrivilegeTests(unittest.TestCase):
    def test_prepared_private_mount_drops_identity_without_root_open(self):
        info = SimpleNamespace(st_uid=99, st_mode=stat.S_IFDIR | 0o700)
        with patch.dict(os.environ, {'DATA_DIR':str(Path.cwd()),'PUID':'99','PGID':'100'}), \
             patch('drop_privileges.Path.lstat', return_value=info), \
             patch('drop_privileges.os.open') as opened, \
             patch('drop_privileges.os.setgroups', create=True) as groups, \
             patch('drop_privileges.os.setgid', create=True) as gid, \
             patch('drop_privileges.os.setuid', create=True) as uid, \
             patch('drop_privileges.os.execv') as execute:
            main()
        opened.assert_not_called()
        groups.assert_called_once_with([])
        gid.assert_called_once_with(100)
        uid.assert_called_once_with(99)
        execute.assert_called_once()

    def test_empty_mount_initialized_then_identity_dropped_before_exec(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('DWELLMIND_TEST_TMPDIR')) as directory:
            Path(directory).chmod(0o755)
            calls=[]
            with patch.dict(os.environ,{'DATA_DIR':directory,'PUID':'99','PGID':'100'}), \
                 patch('drop_privileges.os.fchown',side_effect=lambda *a:calls.append('chown')), \
                 patch('drop_privileges.os.fchmod',side_effect=lambda *a:calls.append('chmod')), \
                 patch('drop_privileges.os.setgroups',side_effect=lambda *a:calls.append('groups')), \
                 patch('drop_privileges.os.setgid',side_effect=lambda *a:calls.append('gid')), \
                 patch('drop_privileges.os.setuid',side_effect=lambda *a:calls.append('uid')), \
                 patch('drop_privileges.os.execv',side_effect=lambda *a:calls.append('exec')):
                main()
            self.assertEqual(calls,['chown','chmod','groups','gid','uid','exec'])

    def test_nonempty_mismatched_mount_not_changed(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('DWELLMIND_TEST_TMPDIR')) as directory:
            Path(directory).chmod(0o755)
            (Path(directory)/'user-file').write_text('keep')
            with patch.dict(os.environ,{'DATA_DIR':directory,'PUID':'99','PGID':'100'}), \
                 patch('drop_privileges.os.fchown') as chown, patch('drop_privileges.os.execv') as execute:
                with self.assertRaises(ValueError):
                    main()
                chown.assert_not_called()
                execute.assert_not_called()
            self.assertEqual((Path(directory)/'user-file').read_text(),'keep')
            self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode),0o755)

    def test_root_identity_and_relative_data_refused(self):
        for changes in [{'PUID':'0','PGID':'100','DATA_DIR':'/data'},
                        {'PUID':'99','PGID':'0','DATA_DIR':'/data'},
                        {'PUID':'99','PGID':'100','DATA_DIR':'relative'}]:
            with patch.dict(os.environ,changes), self.assertRaises(ValueError):
                main()
