from pathlib import Path
import subprocess, sys

def test_repo_smoke():
    assert Path("README.md").exists()
    if Path("run.py").exists():
        subprocess.check_call([sys.executable, "-m", "py_compile", "run.py"])
