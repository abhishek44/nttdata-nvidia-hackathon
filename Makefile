.PHONY: install install-aiq test lint serve demo doctor zip

install:
	uv pip install -e ".[dev]"

install-aiq:
	uv pip install -e ".[dev,aiq]"

test:
	pytest

lint:
	ruff check src tests

serve:
	recallzero serve --reload

demo:
	recallzero demo

doctor:
	recallzero doctor

zip:
	python scripts/build_release.py
