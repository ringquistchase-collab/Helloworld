"""Drives run_local.ps1 through Windows PowerShell with scripted -Answers,
on a copy of the project so option 2 doesn't write node_data/ into the
repo. Skipped where powershell.exe isn't available."""
import glob
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell not available")


@pytest.fixture
def project_copy(tmp_path):
    for path in glob.glob(os.path.join(ROOT, "*.py")) + [os.path.join(ROOT, "run_local.ps1")]:
        shutil.copy(path, tmp_path)
    return tmp_path


def run_ps1(project, answers, extra_env=None):
    env = dict(os.environ, **(extra_env or {}))
    proc = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(project / "run_local.ps1"), "-Answers", answers, "-NoPause"],
        capture_output=True, text=True, timeout=120, env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_codec_self_test_option(project_copy):
    code, out = run_ps1(project_copy, "4")
    assert code == 0, out
    assert "all checks PASSED" in out and "Done." in out


def test_show_key_option(project_copy):
    code, out = run_ps1(project_copy, "3|7")
    assert code == 0, out
    assert "node-7 public signing key:" in out


def test_run_node_with_blank_answers_uses_defaults(project_copy):
    # id 7, port 19596, everything else blank -> defaults; 1 second run
    code, out = run_ps1(project_copy, "2|7|19596|||||1")
    assert code == 0, out
    assert "REAL NETWORK NODE  id=7  bind=127.0.0.1:19596" in out
    assert "chain intact (OK)" in out


def test_bad_input_is_an_error_not_a_command(project_copy):
    code, out = run_ps1(project_copy, "2|7|||x:1 & echo INJECTED||||1")
    assert code != 0
    assert "has an invalid port" in out
    assert "\nINJECTED" not in out


def test_custom_option_rejects_a_folder_and_invalid_choice_fails(project_copy):
    (project_copy / "somedir").mkdir()
    code, out = run_ps1(project_copy, "6|somedir")
    assert code == 1 and "is not a file" in out
    code, out = run_ps1(project_copy, "9")
    assert code == 1 and "No valid choice" in out


def test_python_failure_exit_code_is_reported(project_copy):
    (project_copy / "fails.py").write_text("raise SystemExit(3)\n")
    code, out = run_ps1(project_copy, "6|fails.py")
    assert code == 3 and "exited with code 3" in out
