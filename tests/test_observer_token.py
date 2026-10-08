from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import configure_observer_token


class TokenSetupTests(unittest.TestCase):
    def test_noninteractive_refused_before_secret_prompt(self):
        with patch('sys.argv',['configure_observer_token.py']), patch('sys.stdin.isatty',return_value=False), \
                patch('configure_observer_token.getpass.getpass') as prompt:
            with self.assertRaises(SystemExit):
                configure_observer_token.main()
            prompt.assert_not_called()

    def test_dummy_token_exclusive_owner_only_never_printed(self):
        with tempfile.TemporaryDirectory(prefix='observer-token-mock-',dir=__import__('os').environ.get('DWELLMIND_TEST_TMPDIR')) as directory:
            path = Path(directory)/'dummy-token'
            uid = os.geteuid() or 10001
            output = io.StringIO()
            with patch('sys.argv',['configure_observer_token.py','--file',str(path),'--uid',str(uid)]), \
                    patch('sys.stdin.isatty',return_value=True), patch('sys.stderr.isatty',return_value=True), \
                    patch('configure_observer_token.getpass.getpass',return_value='dummy'+('x'*40)), \
                    patch('configure_observer_token.os.fchown'), redirect_stdout(output):
                configure_observer_token.main()
                with self.assertRaises(FileExistsError):
                    configure_observer_token.main()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            self.assertNotIn('dummy'+('x'*40),output.getvalue())
