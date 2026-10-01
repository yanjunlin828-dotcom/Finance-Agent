import os
import subprocess
import sys
from .helpers import ROOT


def test_cli_sets_utf8_despite_windows_gbk_environment():
    code = "import runpy,sys; sys.argv=['task.py','--help']; runpy.run_path('scripts/s6/task.py',run_name='__main__')"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "gbk"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0
    assert "S6可靠任务" in result.stdout.decode("utf-8")
