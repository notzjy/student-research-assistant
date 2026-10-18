"""Windows启动入口的实际CMD解析回归，不启动GUI、后端或任何用户服务。"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == 'nt', '仅Windows具有实际CMD解释器')
class WindowsBootstrapTests(unittest.TestCase):
    """验证交付脚本在含中文和空格的目录中能完整解析，而非只检查换行字节。"""

    def test_delivered_script_parses_without_truncated_commands(self):
        """保留原脚本字节，仅替换最终启动动作为标记，检查CMD无错误并到达启动步骤。"""
        source = Path(__file__).resolve().parents[1] / '启动助理.cmd'
        raw = source.read_bytes()
        # 禁止测试打开用户窗口；其余条件分支、编码设置与换行均来自真实交付文件。
        probe, count = re.subn(rb'(?m)^start [^\r\n]*', b'echo BOOTSTRAP_READY', raw)
        self.assertEqual(count, 1)
        probe = probe.replace(b'    pause', b'    echo MISSING_ENVIRONMENT')
        with tempfile.TemporaryDirectory(prefix='bootstrap-test-') as temporary:
            directory = Path(temporary) / '中文目录 with spaces'
            executable = directory / '.venv' / 'Scripts' / 'pythonw.exe'
            executable.parent.mkdir(parents=True)
            executable.touch()
            script = directory / '启动助理.cmd'
            script.write_bytes(probe)
            result = subprocess.run(['cmd.exe', '/d', '/c', str(script)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b'BOOTSTRAP_READY', result.stdout)
        self.assertEqual(result.stderr, b'', result.stderr.decode('utf-8', errors='replace'))


if __name__ == '__main__':
    unittest.main()
