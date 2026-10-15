"""The unified browser smoke command is discoverable without running a fixture."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELP = (
    "Usage: node scripts/check_web_governance.mjs [--help]\n"
    "Runs the unified workflow check against a fresh local fixture.\n"
    "Set H5_WEB_URL to the loopback frontend URL (default http://127.0.0.1:5173).\n"
    "Requires fixture-mode API; never contacts live providers.\n"
)


class UnifiedSmokeCommandTests(unittest.TestCase):
    def test_help_is_offline_and_does_not_run_the_browser_journey(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Existing frontend verification requires Node.')
        # Reserve a non-listening local port: even the pre-fix command cannot
        # accidentally reach a running demonstration or modify its data.
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            environment = dict(os.environ, H5_WEB_URL=f'http://127.0.0.1:{reserved.getsockname()[1]}')
            result = subprocess.run(
                [node, str(ROOT / 'scripts/check_web_governance.mjs'), '--help'],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, HELP)
        self.assertEqual(result.stderr, '')
