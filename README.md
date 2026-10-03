# Python Project Bundler

Package a Python project into one distributable `.py` file. The generated
launcher contains a compressed archive of the original source and resource
files. At runtime it extracts them to a temporary directory and starts the
application in a fresh Python interpreter.

Modules remain separate: aliases, relative imports, namespace packages, lazy
imports, valid circular imports, future statements, source encodings, module
metadata, and package resources retain normal Python behavior. Unused modules
are included but never executed merely because they were bundled.

## Requirements

- CPython 3.9 or newer, with the standard library and `zlib` available.
- Third-party dependencies required by the application must be installed in the
  target interpreter, unless their files are already part of the project.
- A writable temporary directory at runtime.

This produces a Python launcher, not a standalone executable containing Python.

## Usage

```bash
git clone https://github.com/henceketh/Python-Project-Bundler.git
cd Python-Project-Bundler
python PyBundle.py myProject main.py output.py
python output.py argument1 argument2
```

The positional arguments remain compatible with the original CLI:

```text
python PyBundle.py <project_directory> <entry_file> <output_file> [-d]
```

`entry_file` is a `.py` path inside the project. Output paths are relative to the
shell's current directory. `-d` / `--debug` lists included files.

For an entry that normally runs as a module, use `--module` to preserve package
context and relative imports:

```bash
python PyBundle.py myProject pkg/__main__.py output.py --module
```

For a `src/` layout, add its import root:

```bash
python PyBundle.py myProject src/pkg/main.py output.py --source-root src --module
```

`--source-root` is repeatable; supplied roots precede the project root. All roots
must be inside the project. Script mode retains the entry directory as the first
import location. Module mode gives the most specific root containing the entry
first priority, then other archived roots, then the caller's current directory.

## Included files and exclusions

All regular files in the project are included, preserving relative paths and
source bytes. This includes data, templates, configuration, package metadata,
and native binaries already present in the tree. No dependency installation or
static import guessing takes place. Build-time application code is never run.

These directory names are ignored at any depth:

```text
.git .hg .svn __pycache__ .venv venv env .tox .nox
.pytest_cache .mypy_cache .ruff_cache node_modules build dist
```

Directories containing `pyvenv.cfg`, bytecode files, the current generated
output, and earlier bundles carrying this tool's header are also excluded.
Symlinks and directory junctions in included paths are rejected with an error.

Use repeatable project-relative globs to omit other files or directories:

```bash
python PyBundle.py myProject main.py output.py --exclude "tests/**" --exclude ".env*"
```

Patterns use `/` separators on every platform. An excluded entry is an error.
Required assets must be placed outside the default ignored directories.

## Runtime behavior and limits

- Source files are extracted with their original layout and file permissions;
  `__file__` points to an existing extracted file. Resources located beside
  modules and resources accessed through `importlib.resources` are available.
- The application inherits arguments, stdin/stdout/stderr, working directory,
  and environment. Interpreter flags are forwarded. Its exit status is returned
  by the launcher. The launcher adds one supervising process.
- Extraction remains available until the application exits, including its
  non-daemon threads, then is removed. Detached processes must not rely on those
  paths after the main application exits. Files written into the extracted tree
  are temporary; store persistent output elsewhere.
- Paths relative to the working directory retain their usual meaning. Use
  `__file__` or resource APIs for bundled assets; original absolute source paths
  cannot be preserved.
- External imports still require a compatible target environment. Native
  extensions and dependent libraries must match the target OS, architecture,
  and Python version; bundling files does not resolve their native dependencies.
- Python's `-E` / `-I` flags intentionally ignore `PYTHONPATH`. In script mode,
  those flags disable added source roots; ordinary entry-directory imports follow
  the interpreter's normal rules.
- Entry syntax is checked using the build interpreter. Other files are preserved
  without parsing so unused or platform-specific files do not prevent building.
  Invalid code still raises its normal error if the application imports it.
- The build assumes the source tree remains stable during collection.

## Build integrity

Output cannot overwrite the entry or another project source/resource file,
including hard-link aliases. Generated outputs can be rebuilt safely without
including themselves. Discovery/read failures are reported instead of silently
omitting files. The entry and generated launcher are syntax-checked before the
output is written to a temporary file and atomically replaced. Failed builds
preserve an existing output.

The success message confirms artifact creation and launcher syntax validation;
it does not claim the application or its external dependencies were runtime-tested.

## Tests

No additional test dependencies are needed:

```bash
python -m unittest discover -s tests -v
```

The suite compares original and bundled fixture programs after moving the source
out of reach. It covers all previously identified import/namespace regressions,
resources and encodings, module and `src/` entry modes, dynamic and lazy imports,
multiprocessing spawn, thread lifetime, interpreter flags, deterministic/repeated
builds, path validation, output protection, and failed-write recovery.

GitHub Actions runs the same suite on Windows and Linux with Python 3.9, 3.11,
and 3.14.
