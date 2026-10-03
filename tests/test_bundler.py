"""Behavioral regressions; run with python -m unittest discover -s tests -v."""
import ast
import base64
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import PyBundle
from fixtures import CASES


class BundlerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bundler_tests_")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.base / "project"
        self.isolated = self.base / "isolated"
        self.project.mkdir()
        self.isolated.mkdir()
        self.output = self.isolated / "bundle.py"

    def write(self, files):
        for name, source in files.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source if isinstance(source, bytes) else source.encode("utf-8"))

    def run_python(self, *args, cwd=None, input=None, seed="0", environment_overrides=None):
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONIOENCODING"] = "utf-8"
        environment.update(environment_overrides or {})
        return subprocess.run([sys.executable, *map(str, args)],
                              cwd=cwd or self.isolated, env=environment, input=input,
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    def build(self, entry="main.py", output=None, **kwargs):
        return PyBundle.build_bundle(self.project, entry, output or self.output, **kwargs)

    def archive(self, output=None):
        tree = ast.parse((output or self.output).read_text(encoding="utf-8"))
        payload = next(ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign) and node.targets[0].id == "_PAYLOAD")
        return zipfile.ZipFile(io.BytesIO(base64.b64decode(payload)))

    def assert_program(self, files, expected="42\n", **kwargs):
        self.write(files)
        self.build(**kwargs)
        self.project.rename(self.base / "unavailable-source")
        result = self.run_python(self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)
        self.assertEqual(result.stderr, "")
        return result

    def test_root_init_runs_once(self):
        self.assert_program({"__init__.py": "print(42)"}, entry="__init__.py")

    def test_entry_can_be_imported(self):
        self.assert_program({
            "main.py": "value = 42\nif __name__ == '__main__':\n from helper import result\n print(result)",
            "helper.py": "from main import value\nresult = value",
        })

    def test_star_import_respects_all(self):
        self.assert_program({
            "main.py": "from helper import *\nprint(value)\nprint('hidden' in globals())",
            "helper.py": "__all__ = ['value']\nvalue = 42\nhidden = 0",
        }, expected="42\nFalse\n")

    def test_future_flags_do_not_leak(self):
        self.assert_program({
            "main.py": "from helper import f\ndef g(x: int): pass\nprint(f.__annotations__['x'], g.__annotations__['x'].__name__)",
            "helper.py": "from __future__ import annotations\ndef f(x: int): pass",
        }, expected="int int\n")

    def test_module_metadata_and_resources(self):
        self.assert_program({
            "main.py": "from pkg.helper import check\nprint(check())",
            "pkg/__init__.py": "",
            "pkg/helper.py": "from pathlib import Path\nfrom importlib import resources\ndef check():\n assert __name__ == 'pkg.helper'\n assert __package__ == 'pkg'\n assert __spec__.name == 'pkg.helper'\n assert Path(__file__).is_file()\n return resources.files('pkg').joinpath('data.txt').read_text()",
            "pkg/data.txt": "42",
        })

    def test_bootstrap_does_not_shadow_application_modules(self):
        self.assert_program({
            "main.py": "import json, pathlib, tempfile\nprint(json.value + pathlib.value + tempfile.value)",
            "json.py": "value = 10", "pathlib.py": "value = 12", "tempfile.py": "value = 20",
        })

    def test_arguments_stdin_stderr_and_exit_code(self):
        self.write({"main.py": "import sys\nprint(sys.argv[1:])\nprint(input())\nprint('error', file=sys.stderr)\nraise SystemExit(7)"})
        self.build()
        result = self.run_python(self.output, "hello world", "--flag", input="42\n")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "['hello world', '--flag']\n42\n")
        self.assertEqual(result.stderr, "error\n")

    def test_working_directory_is_preserved(self):
        self.write({"main.py": "from pathlib import Path\nprint(Path.cwd().name)"})
        self.build()
        result = self.run_python(self.output)
        self.assertEqual(result.stdout, "isolated\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_interpreter_optimization_is_preserved(self):
        self.write({"main.py": "print(__debug__)"})
        self.build()
        result = self.run_python("-O", self.output)
        self.assertEqual(result.stdout, "False\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unbuffered_interpreter_flag_is_preserved(self):
        self.write({"main.py": "import sys\nprint(sys.stdout.write_through)"})
        self.build()
        result = self.run_python("-u", self.output)
        self.assertEqual(result.stdout, "True\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_module_entry_with_relative_import(self):
        self.assert_program({
            "pkg/__init__.py": "", "pkg/__main__.py": "from .helper import value\nprint(value)",
            "pkg/helper.py": "value = 42",
        }, entry="pkg/__main__.py", module=True)

    def test_source_layout_module_entry(self):
        self.assert_program({
            "src/pkg/__init__.py": "", "src/pkg/main.py": "from .helper import value\nprint(value)",
            "src/pkg/helper.py": "value = 42",
        }, entry="src/pkg/main.py", module=True, source_roots=["src"])

    def test_module_entry_cannot_be_shadowed_by_caller_directory(self):
        self.write({"pkg/__init__.py": "", "pkg/main.py": "print(42)"})
        self.build(entry="pkg/main.py", module=True)
        caller_package = self.isolated / "pkg"
        caller_package.mkdir()
        (caller_package / "__init__.py").write_text("")
        (caller_package / "main.py").write_text("print('wrong module')")
        result = self.run_python(self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "42\n")

    def test_module_entry_arguments_and_metadata(self):
        self.write({"pkg/__init__.py": "", "pkg/main.py":
            "import sys\nprint(__name__, __package__, __spec__.name)\nprint(sys.argv[1:])"})
        self.build(entry="pkg/main.py", module=True)
        result = self.run_python(self.output, "argument")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "__main__ pkg pkg.main\n['argument']\n")

    def test_module_entry_uses_its_own_source_root(self):
        self.assert_program({
            "pkg/__init__.py": "", "pkg/main.py": "print('wrong root')",
            "src/pkg/__init__.py": "", "src/pkg/main.py": "print(42)",
        }, entry="src/pkg/main.py", module=True, source_roots=[".", "src"])

    def test_module_init_entry_is_not_replaced_with_package_main(self):
        self.assert_program({"pkg/__init__.py": "if __name__ == '__main__': print(42)",
                             "pkg/__main__.py": "print('wrong entry')"},
                            entry="pkg/__init__.py", module=True)

    def test_script_with_source_root(self):
        self.assert_program({"main.py": "from helper import value\nprint(value)",
                             "src/helper.py": "value = 42"}, source_roots=["src"])

    def test_multiprocessing_spawn(self):
        self.assert_program({"main.py":
            "import multiprocessing as mp\ndef worker(queue):\n queue.put(42)\n"
            "if __name__ == '__main__':\n"
            " context = mp.get_context('spawn')\n queue = context.Queue()\n"
            " child = context.Process(target=worker, args=(queue,))\n child.start()\n"
            " print(queue.get(timeout=10))\n child.join(10)\n assert child.exitcode == 0\n"})

    def test_resources_survive_non_daemon_threads(self):
        self.assert_program({"main.py":
            "from pathlib import Path\nimport threading, time\n"
            "def read(path=Path(__file__).with_name('data')):\n time.sleep(0.1)\n print(path.read_text())\n"
            "threading.Thread(target=read).start()", "data": "42"})

    def test_extraction_is_cleaned_up(self):
        self.write({"main.py": "from pathlib import Path\nprint(Path(__file__).parent)"})
        self.build()
        result = self.run_python(self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(Path(result.stdout.strip()).exists())

    def test_repeated_build_is_identical(self):
        self.write({"main.py": "print(42)"})
        output = self.project / "bundle.py"
        self.build(output=output)
        first = output.read_bytes()
        self.build(output=output)
        self.assertEqual(output.read_bytes(), first)
        self.assertEqual(self.run_python(output).stdout, "42\n")

    def test_other_generated_bundles_are_excluded(self):
        self.write({"main.py": "print(42)"})
        self.build(output=self.project / "old-bundle.py")
        self.write({"legacy.py": "# Holly damnmnnn.\nprint('duplicate')"})
        self.build()
        with self.archive() as archive:
            self.assertEqual(archive.namelist(), ["main.py"])

    def test_builds_are_deterministic_across_hash_seeds(self):
        self.write(dict(CASES[7][1]))
        outputs = []
        for seed in ("0", "1", "7"):
            result = self.run_python(Path(PyBundle.__file__), self.project, "main.py", self.output, seed=seed)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(self.output.read_bytes())
            runtime = self.run_python(self.output, seed=seed)
            self.assertEqual(runtime.returncode, 0, runtime.stderr)
            self.assertEqual(runtime.stdout, "42\n")
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])

    def test_overwriting_any_project_file_is_rejected(self):
        self.write({"main.py": "print(42)", "helper.py": "value = 42"})
        for name in ("main.py", "helper.py"):
            with self.subTest(name=name):
                path = self.project / name
                before = path.read_bytes()
                with self.assertRaises(PyBundle.BundlingError):
                    self.build(output=path)
                self.assertEqual(path.read_bytes(), before)

    def test_output_hardlink_to_source_is_rejected(self):
        self.write({"main.py": "print(42)", "helper.py": "value = 42"})
        try:
            os.link(self.project / "helper.py", self.output)
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaises(PyBundle.BundlingError):
            self.build()
        self.assertEqual((self.project / "helper.py").read_text(), "value = 42")

    def test_excluding_a_source_does_not_allow_overwriting_it(self):
        self.write({"main.py": "print(42)", "helper.py": "value = 42"})
        output = self.project / "helper.py"
        with self.assertRaises(PyBundle.BundlingError):
            self.build(output=output, excludes=["helper.py"])
        self.assertEqual(output.read_text(), "value = 42")

    def test_failed_replace_preserves_old_output_and_cleans_temporary_file(self):
        self.write({"main.py": "print(42)"})
        self.output.write_bytes(b"old valid artifact")
        with mock.patch.object(PyBundle.os, "replace", side_effect=PermissionError("read only")):
            with self.assertRaises(PermissionError):
                self.build()
        self.assertEqual(self.output.read_bytes(), b"old valid artifact")
        self.assertEqual(list(self.isolated.glob(".pybundle_*.tmp")), [])

    def test_invalid_entry_syntax_does_not_touch_output(self):
        self.write({"main.py": "def broken(: pass"})
        self.output.write_bytes(b"old valid artifact")
        with self.assertRaises(SyntaxError):
            self.build()
        self.assertEqual(self.output.read_bytes(), b"old valid artifact")

    def test_entry_encoding_declaration(self):
        self.assert_program({"main.py": b"# coding: latin-1\nprint('caf\xe9')\n"}, expected="café\n")

    def test_exclusions_prune_directories(self):
        self.write({"main.py": "print(42)", ".git/HEAD": "secret", ".venv/pkg.py": "bad",
                    "custom-env/pyvenv.cfg": "home = somewhere", "custom-env/pkg.py": "bad",
                    "__pycache__/main.pyc": b"bad", "node_modules/pkg/file": "bad",
                    "ignore/file.txt": "bad", "secret.txt": "bad", "keep/data.bin": b"\x00\xff"})
        self.build(excludes=["ignore/**", "secret.txt"])
        with self.archive() as archive:
            self.assertEqual(archive.namelist(), ["keep/data.bin", "main.py"])
            self.assertEqual(archive.read("keep/data.bin"), b"\x00\xff")

    def test_cache_substring_in_directory_name_is_allowed(self):
        self.project = self.base / "legitimate__pycache__name"
        self.project.mkdir()
        self.assert_program({"main.py": "from helper import value\nprint(value)", "helper.py": "value = 42"})

    def test_excluded_entry_is_rejected(self):
        self.write({"main.py": "print(42)"})
        with self.assertRaisesRegex(PyBundle.BundlingError, "Entry is excluded"):
            self.build(excludes=["main.py"])
        self.assertFalse(self.output.exists())

    def test_paths_are_validated(self):
        self.write({"main.py": "print(42)", "entry.txt": "print(42)"})
        outside = self.base / "outside.py"
        outside.write_text("print(42)")
        for root, entry, output, options in (
            (self.project / "missing", "main.py", self.output, {}),
            (self.project / "main.py", "main.py", self.output, {}),
            (self.project, outside, self.output, {}),
            (self.project, "missing.py", self.output, {}),
            (self.project, "entry.txt", self.output, {}),
            (self.project, "main.py", self.base / "bundle.txt", {}),
            (self.project, "main.py", self.output, {"source_roots": ["missing"]}),
            (self.project, "main.py", self.output, {"source_roots": [".."]}),
        ):
            with self.subTest(root=root, entry=entry, options=options):
                with self.assertRaises(PyBundle.BundlingError):
                    PyBundle.build_bundle(root, entry, output, **options)

    def test_output_parent_is_created(self):
        self.write({"main.py": "print(42)"})
        output = self.isolated / "new" / "folder" / "bundle.py"
        self.build(output=output)
        result = self.run_python(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "42\n")

    def test_unicode_and_spaces_in_paths(self):
        self.project = self.base / "проект с пробелами"
        self.project.mkdir()
        self.output = self.isolated / "сборка приложения.py"
        self.assert_program({"main.py": "print(42)"})

    def test_linked_source_is_rejected(self):
        self.write({"main.py": "print(42)"})
        target = self.base / "outside.py"
        target.write_text("value = 42")
        try:
            (self.project / "helper.py").symlink_to(target)
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaisesRegex(PyBundle.BundlingError, "links are unsupported"):
            self.build()

    def test_traversal_error_is_reported(self):
        self.write({"main.py": "print(42)"})

        def denied(*args, **kwargs):
            kwargs["onerror"](PermissionError("cannot read directory"))

        with mock.patch.object(PyBundle.os, "walk", side_effect=denied):
            with self.assertRaisesRegex(PermissionError, "cannot read directory"):
                self.build()
        self.assertFalse(self.output.exists())

    def test_cli_reports_errors_without_traceback(self):
        result = self.run_python(Path(PyBundle.__file__), self.project, "missing.py", self.output)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[ERROR]", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.output.exists())

    def test_cli_debug_reports_included_files(self):
        self.write({"main.py": "print(42)"})
        result = self.run_python(Path(PyBundle.__file__), self.project, "main.py", self.output, "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[include] main.py", result.stdout)
        self.assertIn("launcher syntax checked", result.stdout)

    def test_cli_unicode_paths_with_ascii_console(self):
        self.write({"main.py": "print(42)", "данные.txt": "resource"})
        self.output = self.isolated / "сборка.py"
        result = self.run_python(Path(PyBundle.__file__), self.project, "main.py", self.output,
                                 "-d", environment_overrides={"PYTHONIOENCODING": "ascii"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.output.is_file())


def compatibility_test(files):
    def test(self):
        self.write(files)
        original = self.run_python(self.project / "main.py", cwd=self.project)
        self.build()
        self.project.rename(self.base / "unavailable-source")
        bundled = self.run_python(self.output)
        self.assertEqual(bundled.returncode, original.returncode, bundled.stderr)
        self.assertEqual(bundled.stdout, original.stdout)
        if original.returncode:
            self.assertEqual(bundled.stderr.splitlines()[-1].split(":")[0],
                             original.stderr.splitlines()[-1].split(":")[0])
        else:
            self.assertEqual(bundled.stderr, original.stderr)
    return test


for name, files in CASES:
    setattr(BundlerTests, "test_regression_" + name, compatibility_test(files))


if __name__ == "__main__":
    unittest.main()
