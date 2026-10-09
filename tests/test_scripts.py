import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("osmnx")

ROOT = Path(__file__).resolve().parents[1]


def _regenerate_golden():
    spec = importlib.util.spec_from_file_location(
        "regenerate_golden", ROOT / "scripts" / "regenerate_golden.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_golden_script_refuses_out_without_graph(monkeypatch):
    """Argument handling comes before every write here, so `--out`
    alone exits 2 inside argparse, well before a golden can reach
    disk.
    """
    script = _regenerate_golden()
    monkeypatch.setattr(script, "write", lambda *a: pytest.fail("a golden was written"))
    monkeypatch.setattr(sys, "argv", ["regenerate_golden.py", "--out", "x.json"])
    with pytest.raises(SystemExit) as excinfo:
        script.main()
    assert excinfo.value.code == 2


def test_the_sdist_keeps_every_fixture_the_tests_load():
    """The suite also runs against the contents of an unpacked sdist, so
    the exclude entries written for the sdist inside *pyproject.toml*
    may not catch any file under `tests/fixtures/`. This walks that
    directory and runs `fnmatch` on each relative path against all
    of them.
    """
    import fnmatch

    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    excludes = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    for fixture in sorted((ROOT / "tests" / "fixtures").iterdir()):
        path = fixture.relative_to(ROOT).as_posix()
        assert not any(fnmatch.fnmatch(path, p.lstrip("/")) for p in excludes), path


def test_the_golden_script_writes_lf_line_endings(tmp_path):
    """Text mode on Windows turns each newline into CRLF. A golden
    written there would then differ byte for byte from one written
    anywhere else."""
    script = _regenerate_golden()
    out = tmp_path / "golden.json"
    script.write(out, {"b": [1, 2], "a": 1})
    assert out.read_bytes() == b'{\n "a": 1,\n "b": [\n  1,\n  2\n ]\n}\n'
