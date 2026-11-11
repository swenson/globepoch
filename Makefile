.PHONY: install hooks test lint format check build serve clean

install:  ## Install in editable mode with the dev tools
	python -m pip install -e ".[dev]"

hooks:  ## Install git pre-commit hooks (ruff lint + format on commit)
	pre-commit install

test:
	python -m pytest

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

check: lint test

build:  ## Build today's puzzle into _site/, tiles included (downloads scans on first run)
	globepoch build --out _site --tiles-url ""

serve: build  ## Build, then serve _site/ on http://localhost:8000
	python -m http.server -d _site 8000

clean:
	rm -rf build dist _site _tiles .pytest_cache .ruff_cache .coverage htmlcov src/*.egg-info
