"""Test the plugin in an isolated upstream archive; never writes to the live checkout.

python scripts/run_tests.py --hermes-source /path/to/hermes --python /path/to/test-env/bin/python
The interpreter must contain Hermes' test dependencies. Requires git and bash on Linux.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-source", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="hermes-qq-test-") as tmp:
        root = Path(tmp)
        archive = root / "source.tar"
        with archive.open("wb") as dest:
            subprocess.run(["git", "-c", "safe.directory=" + str(args.hermes_source.resolve()),
                            "-C", str(args.hermes_source), "archive", "HEAD"], stdout=dest, check=True)
        source = root / "source"
        source.mkdir()
        with tarfile.open(archive) as contents:
            contents.extractall(source, filter="data")
        for path in (project / "tests").rglob("*.py"):
            dest = source / "tests" / path.relative_to(project / "tests")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.name == "conftest.py" and dest.exists():
                dest.write_text(dest.read_text(encoding="utf-8") + "\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                shutil.copy2(path, dest)
        shutil.copytree(project / "plugins/qq-group-tools", source / "tests/fixtures/qq-plugin")
        files = [str(p.relative_to(project)) for p in (project / "tests").rglob("test_*.py")]
        env = dict(os.environ)
        # The archive is a new checkout; use the explicitly supplied test
        # interpreter rather than inheriting the live CLI's activation.
        for key in list(env):
            if key.startswith("__HERMES_"):
                env.pop(key)
        # Preserve the venv executable path: resolving its symlink loses the venv.
        env["HERMES_PYTHON"] = str(args.python.absolute())
        result = subprocess.run(["bash", "scripts/run_tests.sh", *sorted(files), "--tb=short"], cwd=source, env=env)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
