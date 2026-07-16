import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_self_contained_launcher_emits_schema() -> None:
    result = subprocess.run(
        [str(REPO_ROOT / "shm"), "schema"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    schema = json.loads(result.stdout)
    assert schema


def test_setup_installs_working_launcher(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    env = os.environ | {"SHM_BIN_DIR": str(bin_dir)}
    env.pop("SUPERHUMAN_MAIL_CONFIG", None)
    subprocess.run(
        [str(REPO_ROOT / "scripts/setup.sh")],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    launcher = bin_dir / "shm"
    assert launcher.is_symlink()
    result = subprocess.run(
        [str(launcher), "schema"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)
