"""Legacy record readers must retain the selector measured by pytest."""
import sys
import subprocess
import pytest

import batch_gate
import verification_record as record


NODE = 'tests/test_case.py::test_case[alpha - beta]'


def test_record_preserves_parameter_spaces_and_reason_delimiter():
    output = f'FAILED {NODE} - AssertionError\n= 1 failed in 0.10s =\n'
    assert record.parse_pytest_output(output)['failing_nodes'] == [NODE]


def test_batch_gate_does_not_excuse_a_truncated_parameter(tmp_path):
    capture = tmp_path / 'output'
    capture.write_text(f'FAILED {NODE} - AssertionError\n')
    ids = batch_gate._parse_failing_node_ids(capture)
    assert ids == [NODE]
    assert batch_gate._undeclared_failures(ids, [
        {'node_id': 'tests/test_case.py::test_case[alpha'}]) == [NODE]


def test_unbalanced_parameter_never_becomes_a_selector(tmp_path):
    capture = tmp_path / 'output'
    capture.write_text('FAILED tests/test_case.py::test_case[alpha\n')
    assert batch_gate._parse_failing_node_ids(capture) == []
    assert record.parse_pytest_output(capture.read_text() +
        '= 1 failed in 0.10s =\n')['failing_nodes'] == []


def _actual_red(tmp_path):
    (tmp_path / 'test_case.py').write_text(
        'import pytest\n@pytest.mark.parametrize("value", [1], ids=["alpha beta"])\n'
        'def test_case(value):\n    assert value == 0\n')
    return 'test_case.py::test_case[alpha beta]', f'{sys.executable} -m pytest -q'


def test_actual_head_alone_keeps_parameter_literal(tmp_path):
    node, command = _actual_red(tmp_path)
    assert record.run_head_alone(tmp_path, [node], command, timeout=10) == {
        node: 'failed'}


def test_actual_head_rerun_keeps_parameter_literal(tmp_path):
    node, command = _actual_red(tmp_path)
    counts, bounded = record.run_head_reruns(tmp_path, [node], command,
        K=1, budget_s=10)
    assert counts == {node: (1, 1)} and not bounded


def test_unreadable_head_rerun_cannot_settle_a_failure(tmp_path):
    command = f'{sys.executable} -c "raise SystemExit(4)"'
    with pytest.raises(subprocess.SubprocessError, match='known outcome'):
        record.run_head_reruns(tmp_path, ['test_case.py::test_case'], command,
                              K=1, budget_s=10)
    assert record.run_head_alone(tmp_path, ['test_case.py::test_case'],
        command, timeout=10) == {'test_case.py::test_case': 'unmeasured'}


def test_actual_at_base_keeps_parameter_literal(tmp_path):
    node, command = _actual_red(tmp_path)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'test_case.py'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Fixture',
        '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'base'], check=True)
    base = subprocess.check_output(['git', '-C', str(tmp_path), 'rev-parse',
                                    'HEAD'], text=True).strip()
    assert record.run_at_base(tmp_path, base, [node], command, timeout=10) == {
        node: 'failed'}
