"""Bundle Python sources and resources into a single extraction launcher."""
import argparse
import base64
import fnmatch
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import zipfile

BUNDLE_HEADER = "# Python Project Bundler format: 2\n"
LEGACY_HEADER = b"# Holly damnmnnn."
EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env",
    ".tox", ".nox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "build", "dist",
})

# A fresh interpreter prevents bootstrap imports from shadowing application
# modules named json.py, pathlib.py, etc. The supervisor keeps extracted files
# alive until the application and its non-daemon threads have finished.
BOOTSTRAP = '''import base64 as _base64
import io as _io
import os as _os
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile
import zipfile as _zipfile


def _run_bundle():
    with _tempfile.TemporaryDirectory(prefix="pybundle_") as _directory:
        with _zipfile.ZipFile(_io.BytesIO(_base64.b64decode(_PAYLOAD))) as _archive:
            for _member in _archive.infolist():
                _parts = _member.filename.split("/")
                if any(_part in ("", ".", "..") for _part in _parts):
                    raise RuntimeError("Invalid path in bundle")
                _target = _os.path.abspath(_os.path.join(_directory, *_parts))
                if _os.path.commonpath([_directory, _target]) != _directory:
                    raise RuntimeError("Path escapes bundle directory")
                _archive.extract(_member, _directory)
                _mode = (_member.external_attr >> 16) & 0o777
                if _mode:
                    _os.chmod(_target, _mode)
        _environment = _os.environ.copy()
        _roots = [_os.path.join(_directory, *_root.split("/")) for _root in _ROOTS]
        if _environment.get("PYTHONPATH"):
            _roots.append(_environment["PYTHONPATH"])
        _environment["PYTHONPATH"] = _os.pathsep.join(_roots)
        # This stdlib helper carries interpreter flags through to the child,
        # just as multiprocessing does in CPython.
        _command = [_sys.executable] + _subprocess._args_from_interpreter_flags()
        if getattr(_sys.stdout, "write_through", False):
            _command.append("-u")
        if _MODULE:
            # Unlike plain -m, put archived roots before the caller's cwd.
            # runpy's interpreter entry helper keeps real __main__ semantics.
            _runner = ("import sys; sys.path[:0] = " + repr(_roots[:len(_ROOTS)])
                       + "; import runpy; runpy._run_module_as_main(" + repr(_MODULE) + ")")
            _command += ["-c", _runner]
        else:
            _command.append(_os.path.join(_directory, *_ENTRY.split("/")))
        _command.extend(_sys.argv[1:])
        _child = _subprocess.Popen(_command, env=_environment)
        try:
            _status = _child.wait()
        except KeyboardInterrupt:
            try:
                _status = _child.wait(timeout=3)
            except _subprocess.TimeoutExpired:
                _child.terminate()
                _child.wait()
                _status = 130
        if _status < 0:
            _status = 128 - _status
        return _status


if __name__ == "__main__":
    raise SystemExit(_run_bundle())
'''


class BundlingError(ValueError):
    """An actionable input or discovery error."""


def _excluded(relative, patterns):
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def _generated(path):
    with path.open("rb") as source:
        header = source.readline(100)
    return header.rstrip() in {BUNDLE_HEADER.encode("ascii").rstrip(), LEGACY_HEADER}


def collect_files(project_dir, output_file, excludes=()):
    """Inventory files deterministically; never follow links or execute source."""
    files = []

    def walk_error(error):
        raise error

    for directory, dirs, names in os.walk(project_dir, onerror=walk_error):
        root = Path(directory)
        kept = []
        for name in sorted(dirs):
            path = root / name
            relative = path.relative_to(project_dir).as_posix()
            if (name in EXCLUDED_DIRECTORIES or (path / "pyvenv.cfg").is_file()
                    or _excluded(relative, excludes) or _excluded(relative + "/", excludes)):
                continue
            if path.is_symlink() or path.resolve() != path:
                raise BundlingError(f"Directory links are unsupported: {relative}")
            kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = root / name
            relative = path.relative_to(project_dir).as_posix()
            if _excluded(relative, excludes) or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_symlink():
                raise BundlingError(f"File links are unsupported: {relative}")
            if not stat.S_ISREG(path.stat().st_mode):
                raise BundlingError(f"Not a regular file: {relative}")
            is_output = path == output_file or (
                output_file.exists() and os.path.samefile(path, output_file))
            if is_output:
                if not _generated(path):
                    raise BundlingError(f"Output would overwrite a project file: {relative}")
                continue
            if path.suffix == ".py" and _generated(path):
                continue
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(project_dir).as_posix())


def _module_name(entry_relative):
    parts = list(entry_relative.with_suffix("").parts)
    if not parts or any(not part.isidentifier() for part in parts):
        raise BundlingError("Entry has no valid module name; use script mode instead")
    return ".".join(parts)


def build_bundle(project_dir, entry_file, output_file, *, excludes=(),
                 source_roots=(), module=False, debug=False):
    """Build atomically, without executing source, and return archived file count.

    Entry syntax is checked. Other source is preserved verbatim: optional,
    platform-specific and unused modules need not parse on the build host.
    """
    raw_root = Path(project_dir)
    if not raw_root.is_dir():
        raise BundlingError(f"Project directory not found: {raw_root}")
    project_dir = raw_root.resolve()
    raw_entry = Path(entry_file)
    if not raw_entry.is_absolute():
        raw_entry = project_dir / raw_entry
    if raw_entry.is_symlink():
        raise BundlingError("Entry must not be a symbolic link")
    entry_file = raw_entry.resolve()
    try:
        entry_relative = entry_file.relative_to(project_dir)
    except ValueError as error:
        raise BundlingError("Entry must be inside the project directory") from error
    if not entry_file.is_file() or entry_file.suffix != ".py":
        raise BundlingError(f"Entry must be an existing .py file: {entry_file}")

    raw_output = Path(output_file)
    if raw_output.is_symlink():
        raise BundlingError("Output must not be a symbolic link")
    output_file = raw_output.resolve()
    if output_file.suffix != ".py":
        raise BundlingError("Output must have a .py extension")
    if output_file == entry_file or (output_file.exists()
                                     and os.path.samefile(output_file, entry_file)):
        raise BundlingError("Output must not overwrite the entry file")
    if output_file.exists() and not output_file.is_file():
        raise BundlingError("Output must be a regular file")
    if (output_file.exists() and project_dir in output_file.parents
            and not _generated(output_file)):
        raise BundlingError("Output would overwrite an existing project file")

    roots = []
    for raw_source_root in source_roots:
        path = (project_dir / raw_source_root).resolve()
        try:
            relative = path.relative_to(project_dir).as_posix()
        except ValueError as error:
            raise BundlingError("Source roots must be inside the project") from error
        if not path.is_dir():
            raise BundlingError(f"Source root not found: {raw_source_root}")
        if relative not in roots:
            roots.append(relative)
    if "." not in roots:
        roots.append(".")

    files = collect_files(project_dir, output_file, excludes)
    if entry_file not in files:
        raise BundlingError("Entry is excluded, inside an ignored directory, or a generated bundle")
    compile(entry_file.read_bytes(), str(entry_file), "exec", dont_inherit=True)
    module_name = None
    if module:
        candidates = [(entry_relative.relative_to(root), root.as_posix())
                      for root in map(Path, roots) if root in entry_relative.parents]
        relative_module, entry_root = min(candidates, key=lambda item: len(item[0].parts))
        module_name = _module_name(relative_module)
        roots.remove(entry_root)
        roots.insert(0, entry_root)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in files:
            relative = path.relative_to(project_dir).as_posix()
            if debug:
                print(f"[include] {relative}")
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | stat.S_IMODE(path.stat().st_mode)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    chunks = "\n".join(repr(payload[offset:offset + 100])
                       for offset in range(0, len(payload), 100))
    source = (BUNDLE_HEADER + "# Generated launcher; requires Python 3.9+.\n"
              + f"_ENTRY = {entry_relative.as_posix()!r}\n"
              + f"_MODULE = {module_name!r}\n_ROOTS = {roots!r}\n"
              + "_PAYLOAD = (\n" + chunks + "\n)\n\n" + BOOTSTRAP)
    compile(source, str(output_file), "exec", dont_inherit=True)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=output_file.parent, prefix=".pybundle_",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output_file)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return len(files)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_directory")
    parser.add_argument("entry_file", help=".py path relative to the project directory")
    parser.add_argument("output_file")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                        help="exclude a project-relative path/glob; repeatable")
    parser.add_argument("--source-root", action="append", default=[], metavar="PATH",
                        help="add an import root inside the project, e.g. src; repeatable")
    parser.add_argument("--module", action="store_true",
                        help="launch the entry as a module (like python -m)")
    args = parser.parse_args(argv)
    try:
        count = build_bundle(args.project_directory, args.entry_file, args.output_file,
                             excludes=args.exclude, source_roots=args.source_root,
                             module=args.module, debug=args.debug)
    except (OSError, ValueError, SyntaxError, zipfile.BadZipFile) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    print(f"[OK] Created {args.output_file} ({count} files; launcher syntax checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
