.PHONY: install test run experiment lint clean

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest -q

run:
	python -m uvicorn aeris.api.app:app --host 127.0.0.1 --port 8000 --reload

experiment:
	python examples/run_experiment.py

example:
	python examples/run_latency_reroute.py

clean:
	python -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in map(pathlib.Path, ['.pytest_cache','htmlcov','dist','build','data'])]; print('cleaned')"
