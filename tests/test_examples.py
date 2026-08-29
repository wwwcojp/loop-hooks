"""examples/ の同梱物のテスト(0.10.0)。

examples/verify.py は利用者が scripts/ にコピーして使うテンプレート。ここでは一時ディレクトリの
scripts/verify.py にコピーし、STAGES を差し替えて subprocess で実行し、
出力形式と終了コードを固定する。
"""

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.lib import config  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "examples" / "verify.py"
MARKER_RE = re.compile(r"# --- STAGES BEGIN ---\n.*?# --- STAGES END ---\n", re.S)


def _run_template(tmp_path: Path, stages_src: str, *args: str) -> subprocess.CompletedProcess[str]:
    """テンプレートを tmp_path/scripts/verify.py に置き、
    STAGES を stages_src に差し替えて実行する。"""
    src = TEMPLATE.read_text(encoding="utf-8")
    assert MARKER_RE.search(src), "テンプレートに STAGES BEGIN/END マーカーが無い"
    src = MARKER_RE.sub("# --- STAGES BEGIN ---\n" + stages_src + "# --- STAGES END ---\n", src)
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "verify.py").write_text(src, encoding="utf-8")
    return subprocess.run(  # noqa: S603 -- argv は固定
        [sys.executable, str(scripts / "verify.py"), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


OK_STAGES = (
    "STAGES: dict[str, list[Check]] = {\n"
    '    "quick": [Check("a", ["true"]), Check("b", ["true"])],\n'
    '    "slow": [Check("c", ["true"])],\n'
    "}\n"
)


def test_全部通ればexit0でokが並ぶ(tmp_path):
    r = _run_template(tmp_path, OK_STAGES, "quick")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines() == ["[verify] a: ok", "[verify] b: ok"]


def test_失敗したらexit1でFAIL行が出て後続は走らない(tmp_path):
    marker = tmp_path / "ran-c"
    stages = (
        "STAGES: dict[str, list[Check]] = {\n"
        '    "quick": [Check("a", ["true"]), Check("b", ["false"]), Check("c", ["touch", '
        + repr(str(marker))
        + "])],\n"
        "}\n"
    )
    r = _run_template(tmp_path, stages, "quick")
    assert r.returncode == 1
    lines = r.stdout.splitlines()
    assert lines[0] == "[verify] a: ok"
    assert lines[1] == "[verify] b: FAIL (exit 1)"
    assert not marker.exists()


def test_失敗時は出力の末尾が続く(tmp_path):
    stages = (
        "STAGES: dict[str, list[Check]] = {\n"
        '    "quick": [Check("a", ["sh", "-c", "echo DETAIL_LINE; exit 3"])],\n'
        "}\n"
    )
    r = _run_template(tmp_path, stages, "quick")
    assert r.returncode == 1
    assert "[verify] a: FAIL (exit 3)" in r.stdout
    assert "DETAIL_LINE" in r.stdout


def test_コマンドが無ければcommand_not_found(tmp_path):
    stages = (
        "STAGES: dict[str, list[Check]] = {\n"
        '    "quick": [Check("a", ["no-such-command-loop-hooks"])],\n'
        "}\n"
    )
    r = _run_template(tmp_path, stages, "quick")
    assert r.returncode == 1
    assert "[verify] a: FAIL (command not found: no-such-command-loop-hooks)" in r.stdout


def test_空のコマンドはFAILになる(tmp_path):
    stages = 'STAGES: dict[str, list[Check]] = {\n    "quick": [Check("a", [])],\n}\n'
    r = _run_template(tmp_path, stages, "quick")
    assert r.returncode == 1
    assert "[verify] a: FAIL (empty command)" in r.stdout


def test_allは全stageを定義順に走らせる(tmp_path):
    r = _run_template(tmp_path, OK_STAGES, "all")
    assert r.returncode == 0
    assert r.stdout.splitlines() == ["[verify] a: ok", "[verify] b: ok", "[verify] c: ok"]


def test_未知のstageはexit2(tmp_path):
    r = _run_template(tmp_path, OK_STAGES, "nope")
    assert r.returncode == 2
    assert "unknown stage: nope (known: quick, slow, all)" in r.stderr


def test_空文字のstageはexit2(tmp_path):
    r = _run_template(tmp_path, OK_STAGES, "")
    assert r.returncode == 2
    assert "unknown stage:" in r.stderr


def test_print_ciはcheckごとに1行(tmp_path):
    r = _run_template(tmp_path, OK_STAGES, "--print-ci", "quick")
    assert r.returncode == 0
    assert r.stdout.splitlines() == [shlex.join(["true"]), shlex.join(["true"])]
    r = _run_template(tmp_path, OK_STAGES, "--print-ci")
    assert r.returncode == 0
    assert r.stdout.splitlines() == ["true", "true", "true"]


def test_print_ciは引数をshellクオートする(tmp_path):
    stages = (
        "STAGES: dict[str, list[Check]] = {\n"
        '    "quick": [Check("a", ["echo", "hello world"])],\n'
        "}\n"
    )
    r = _run_template(tmp_path, stages, "--print-ci", "quick")
    assert r.returncode == 0
    assert r.stdout.splitlines() == ["echo 'hello world'"]


def test_テンプレートは同梱のSTAGESでヘルプが出る():
    r = subprocess.run(  # noqa: S603
        [sys.executable, str(TEMPLATE), "--help"], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0
    assert "quick" in r.stdout and "--print-ci" in r.stdout


def test_テンプレートは200行以内でstdlibのみ():
    src = TEMPLATE.read_text(encoding="utf-8")
    assert len(src.splitlines()) <= 200
    imports = re.findall(r"^(?:from|import)\s+([A-Za-z_][A-Za-z0-9_.]*)", src, re.M)
    assert set(imports) <= {
        "argparse",
        "dataclasses",
        "pathlib",
        "shlex",
        "subprocess",
        "sys",
        "__future__",
    }


EXAMPLES = REPO_ROOT / "examples"
EXPECTED_COMMANDS = {
    "python-uv": "uv run python scripts/verify.py quick",
    "node-bun": "bun run lint && bun test",
    "rust-cargo": "cargo fmt --check && cargo clippy -q -- -D warnings && cargo test -q",
    "go": "gofmt -l . | (! grep .) && go vet ./... && go test ./...",
}

# (timeout_sec, watch, ignore) — config.load() は gate に既にあるキーを上書きするだけなので
# (hooks/lib/config.py の _validate: merged.update(gate))、ここは JSON の生の値と一致する。
EXPECTED_GATE = {
    "python-uv": (300, ["*.py", "pyproject.toml"], [".venv/", ".hypothesis/"]),
    "node-bun": (
        300,
        ["*.ts", "*.tsx", "package.json", "*tsconfig*.json"],
        ["node_modules/", "dist/"],
    ),
    "rust-cargo": (600, ["*.rs", "Cargo.toml", "Cargo.lock"], ["target/"]),
    "go": (300, ["*.go", "go.mod", "go.sum"], ["vendor/"]),
}


def test_設定例は4つ():
    dirs = sorted(p.name for p in EXAMPLES.iterdir() if (p / ".loop-hooks.json").is_file())
    assert dirs == sorted(EXPECTED_COMMANDS)


@pytest.mark.parametrize("stack", sorted(EXPECTED_COMMANDS))
def test_設定例はconfigの検証を通る(stack, tmp_path):
    # git リポジトリではない一時ディレクトリにコピーして作業ツリー版として読む(HEAD 優先を避ける)
    shutil.copy(EXAMPLES / stack / ".loop-hooks.json", tmp_path / ".loop-hooks.json")
    cfg = config.load(str(tmp_path))
    assert cfg is not None and "_error" not in cfg, cfg
    assert cfg["gate"]["command"] == EXPECTED_COMMANDS[stack]
    assert (
        cfg["gate"]["timeout_sec"],
        cfg["gate"]["watch"],
        cfg["gate"]["ignore"],
    ) == EXPECTED_GATE[stack]


def test_READMEはexamplesへリンクする():
    for name in ("README.md", "README.ja.md"):
        assert "examples/README.md" in (REPO_ROOT / name).read_text(encoding="utf-8"), name
    assert (EXAMPLES / "README.md").is_file()
