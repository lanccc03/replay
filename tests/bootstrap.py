import pathlib
import shutil
import sys
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

TMP_ROOT = ROOT / ".tmp" / "tests"
TMP_ROOT.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TMP_ROOT)


class _WorkspaceTemporaryDirectory:
    def __init__(
        self,
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | None = None,
        ignore_cleanup_errors: bool = False,
    ) -> None:
        base_dir = pathlib.Path(dir) if dir else TMP_ROOT
        base_dir.mkdir(parents=True, exist_ok=True)
        name = f"{prefix or 'tmp'}{uuid.uuid4().hex}{suffix or ''}"
        self.name = str(base_dir / name)
        pathlib.Path(self.name).mkdir(parents=True, exist_ok=False)
        self._ignore_cleanup_errors = ignore_cleanup_errors

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        shutil.rmtree(self.name, ignore_errors=self._ignore_cleanup_errors)


tempfile.TemporaryDirectory = _WorkspaceTemporaryDirectory  # type: ignore[assignment]

