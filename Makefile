ifeq ($(OS),Windows_NT)
	PY := python
	VENV_PY := .venv/Scripts/python.exe
	VENV_PIP := .venv/Scripts/pip.exe
else
	PY := python3
	VENV_PY := .venv/bin/python
	VENV_PIP := .venv/bin/pip
endif

.PHONY: install test demo agent-demo server clean

install:
	$(PY) -m venv .venv
	$(VENV_PIP) install -e ".[test]"

test:
	$(VENV_PY) -m pytest tests/ -q

server:
	$(VENV_PY) examples/run_server.py

demo:
	$(VENV_PY) examples/triage_direct.py

agent-demo:
	$(VENV_PY) examples/run_demo.py

clean:
	$(VENV_PY) -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in list(pathlib.Path('.').rglob('__pycache__')) + [pathlib.Path('.pytest_cache')]]"
