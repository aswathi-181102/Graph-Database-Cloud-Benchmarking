.PHONY: setup dataset doctor bench report test lint up down clean

setup:
	python3 -m venv .venv
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
	docker compose up -d

down:
	docker compose down -v

clean:
	rm -rf data/prepared
	find . -name __pycache__ -prune -exec rm -rf {} +
