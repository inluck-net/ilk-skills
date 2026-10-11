"""Only direct test invocations receive the test-environment adapter."""
import pytest
from bounded_run import _is_direct_pytest

@pytest.mark.parametrize('argv,shell,expected',[
    (['python3','-m','pytest','-q','tests/test_one.py'],False,True),
    (['/venv/bin/python3.9','-I','-m','pytest','-q'],False,True),
    (['pytest','-q','tests/test_one.py'],False,True),
    (['/bin/bash','-c','python3 -m pytest -q tests/test_one.py'],False,True),
    ('python3 -m pytest -q -k "alpha or beta"',True,True),
    ('python3 -m pytest -q; echo passed',True,False),
    ('python3 -m pytest -q && echo passed',True,False),
    ('python3 -m pytest -q | cat',True,False),
    ('python3 -m pytest -q > output.log',True,False),
    (['python3','-c','import pytest; pytest.main()'],False,False),
    (['python3','script.py'],False,False),
    (['/usr/bin/env','python3','-m','pytest'],False,False),
    ('python3 -m pytest -q "unterminated',True,False),
])
def test_direct_pytest_adapter_boundary(argv,shell,expected):
    assert _is_direct_pytest(argv,shell=shell) is expected
