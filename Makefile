.PHONY: install test lint typecheck run clean

install:
	pip install -e .

test:
	python -m unittest discover -s tests -v

lint:
	ruff check src tests

typecheck:
	mypy src

run:
	python -m hope_hash $(ARGS)

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
