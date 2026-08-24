.PHONY: setup dataset doctor bench report test lint up down clean

# 3.13 rather than whatever python3 points at: see pyproject for the wheel gap.
PYTHON ?= python3.13

setup:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -U pip
	.venv/bin/pip install -e ".[dev]"

dataset:
	.venv/bin/python -m graphbench dataset prepare

doctor:
	.venv/bin/python -m graphbench doctor

bench:
	.venv/bin/python -m graphbench run

report:
	.venv/bin/python -m graphbench report

test:
	.venv/bin/pytest --cov=graphbench

lint:
	.venv/bin/ruff check src tests

up:
	docker compose up -d --wait

down:
	docker compose down -v

clean:
	rm -rf data/prepared
	find . -name __pycache__ -prune -exec rm -rf {} +
