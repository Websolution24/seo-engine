"""Offline public-release checks: no engine import, .env read or API request."""
from __future__ import annotations

import argparse
import ast
import contextlib
import csv
import io
import tempfile
import unittest
import unicodedata
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("analyze_serp.py", "analyze_openai.py", "analyze_urls.py", "src_utils.py")


def source_tree(filename):
    return ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"), filename=filename)


def functions(filename, names, **extra):
    """Only selected definitions are compiled; all module side effects stay inert."""
    selected = [node for node in source_tree(filename).body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in selected} != set(names):
        raise AssertionError("Requested source functions are missing")
    for node in selected:
        node.decorator_list = []
    namespace = {"argparse": argparse, "Path": Path, "csv": csv,
                 "unicodedata": unicodedata, "urlparse": urlparse, "logger": Mock(), **extra}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), filename, "exec"), namespace)
    return namespace


class PaidAPIGuards(unittest.TestCase):
    def test_no_flag_exits_before_env_keys_input_or_network(self):
        for filename in ("analyze_serp.py", "analyze_openai.py"):
            with self.subTest(filename=filename):
                forbidden = Mock(side_effect=AssertionError("Unexpected side effect before consent"))
                namespace = functions(filename, ["main"], INPUT_KEYWORDS_TXT=Path("unused.txt"),
                                      load_project_env=forbidden, get_env=forbidden, get_defaults=forbidden,
                                      read_json=forbidden, iso_now=forbidden, requests=forbidden)
                with patch("sys.argv", [filename]), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        namespace["main"]()
                self.assertEqual(raised.exception.code, 2)
                forbidden.assert_not_called()

    def test_help_exits_without_env_or_billing(self):
        for filename in ("analyze_serp.py", "analyze_openai.py"):
            with self.subTest(filename=filename):
                forbidden = Mock(side_effect=AssertionError("Unexpected ENV load"))
                namespace = functions(filename, ["main"], INPUT_KEYWORDS_TXT=Path("unused.txt"), load_project_env=forbidden)
                output = io.StringIO()
                with patch("sys.argv", [filename, "--help"]), contextlib.redirect_stdout(output):
                    with self.assertRaises(SystemExit) as raised:
                        namespace["main"]()
                self.assertEqual(raised.exception.code, 0)
                self.assertIn("--allow-paid-api", output.getvalue())
                forbidden.assert_not_called()


class LocalEnvironmentOnly(unittest.TestCase):
    def test_local_env_path_is_explicit_and_missing_file_does_nothing(self):
        class RootPath:
            def __init__(self, path): self.path = path
            def __truediv__(self, name):
                if name != ".env": raise AssertionError("Wrong ENV name")
                return self.path

        for filename in ("analyze_serp.py", "src_utils.py"):
            for exists in (False, True):
                with self.subTest(filename=filename, exists=exists):
                    path = Mock()
                    path.is_file.return_value = exists
                    loader = Mock()
                    namespace = functions(filename, ["load_project_env"], PROJECT_ROOT=RootPath(path), load_dotenv=loader)
                    namespace["load_project_env"]()
                    if exists: loader.assert_called_once_with(dotenv_path=path)
                    else: loader.assert_not_called()

    def test_no_dotenv_execution_at_module_scope(self):
        for filename in ("analyze_serp.py", "src_utils.py"):
            for statement in source_tree(filename).body:
                if isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
                    continue
                calls = [node for node in ast.walk(statement) if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Name) and node.func.id in {"load_dotenv", "load_project_env"}]
                self.assertEqual(calls, [], filename)


class SpreadsheetSafety(unittest.TestCase):
    def setUp(self):
        self.namespace = functions("src_utils.py", ["safe_spreadsheet_value", "write_csv", "write_xlsx_sheets"])
        self.safe = self.namespace["safe_spreadsheet_value"]

    def test_formulas_after_whitespace_and_controls_are_text(self):
        for value in ("=1+1", " +SUM(A1:A2)", "\t@SUM(A1:A2)", "\r\n-2", "\x01=1", "\u200b\ufeff =1"):
            with self.subTest(value=repr(value)):
                self.assertTrue(self.safe(value).startswith("'"))
        for value in (5, -2.5, None, True):
            self.assertIs(self.safe(value), value)
        self.assertEqual(self.safe("ordinary text"), "ordinary text")

    def test_csv_sanitizes_headers_and_values(self):
        with tempfile.TemporaryDirectory(prefix="_tmp_release_", dir=ROOT / "tests") as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to((ROOT / "tests").resolve()))
            path = Path(temp) / "synthetic.csv"
            self.namespace["write_csv"](path, [{"=header": " \t=HYPERLINK(\"x\")", "number": -2.5}])
            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file))
            self.assertEqual(rows[0], ["'=header", "number"])
            self.assertTrue(rows[1][0].startswith("'"))
            self.assertEqual(rows[1][1], "-2.5")

    def test_xlsx_keeps_numbers_and_prevents_formula_cells(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            self.skipTest("openpyxl not installed; no installation is attempted")
        with tempfile.TemporaryDirectory(prefix="_tmp_release_", dir=ROOT / "tests") as temp:
            self.assertTrue(Path(temp).resolve().is_relative_to((ROOT / "tests").resolve()))
            path = Path(temp) / "synthetic.xlsx"
            self.namespace["write_xlsx_sheets"](path, {"test": [{"=header": "\x01=1+1", "number": -2.5}]})
            workbook = load_workbook(path, data_only=False)
            try:
                sheet = workbook["test"]
                self.assertEqual(sheet["A1"].value, "'=header")
                self.assertEqual(sheet["A2"].value, "'=1+1")
                self.assertEqual(sheet["A2"].data_type, "s")
                self.assertEqual(sheet["B2"].value, -2.5)
                self.assertEqual(sheet["B2"].data_type, "n")
            finally:
                workbook.close()


class ResultContract(unittest.TestCase):
    def test_absolute_group_and_legacy_ranks_are_distinct(self):
        namespace = functions("analyze_serp.py", ["_safe_domain", "parse_serp"])
        result = namespace["parse_serp"]("synthetic", {"items": [
            {"type": "organic", "rank_absolute": 7, "rank_group": 2, "url": "https://example.org/a"},
            {"type": "organic", "position": 11, "url": "https://example.org/b"},
        ]})["organic_results"]
        self.assertEqual((result[0]["rank"], result[0]["rank_absolute"], result[0]["rank_group"]), (7, 7, 2))
        self.assertEqual(result[1]["rank"], 11)
        self.assertIsNone(result[1]["rank_absolute"])
        self.assertIsNone(result[1]["rank_group"])

    def test_experimental_column_names_and_all_scripts_compile(self):
        for filename in SCRIPTS:
            compile(source_tree(filename), filename, "exec")
        values = {node.value for node in ast.walk(source_tree("analyze_openai.py"))
                  if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertIn("experimental_difficulty_proxy", values)
        self.assertIn("experimental_opportunity_proxy", values)
        self.assertNotIn("difficulty_score", values)
        self.assertNotIn("opportunity_score", values)


if __name__ == "__main__":
    unittest.main()
