"""Repeated scope scans reuse parsing, never reuse results after source changes."""
import ast
import subprocess

from test_importers import importer_tests


def _project(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    tests = tmp_path / 'tests'
    tests.mkdir()
    file = tests / 'test_imports.py'
    file.write_text('import original\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    return file


def test_identical_source_is_parsed_once(tmp_path, monkeypatch):
    _project(tmp_path)
    calls = []
    original_parse = ast.parse

    def counted(source, *args, **kwargs):
        if source == "import original\n":
            calls.append(source)
        return original_parse(source, *args, **kwargs)

    monkeypatch.setattr(ast, 'parse', counted)
    assert importer_tests(tmp_path, ['original.py']) == ['tests/test_imports.py']
    assert importer_tests(tmp_path, ['other.py']) == []
    assert calls == ['import original\n']


def test_changed_source_invalidates_parse_even_with_same_size(tmp_path):
    file = _project(tmp_path)
    assert importer_tests(tmp_path, ['original.py']) == ['tests/test_imports.py']
    file.write_text('import replaced\n')
    assert importer_tests(tmp_path, ['original.py']) == []
    assert importer_tests(tmp_path, ['replaced.py']) == ['tests/test_imports.py']
