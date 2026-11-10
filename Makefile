.PHONY: install test lint data eval eval-dev ui demo docker

install:
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev,ui]"

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check src tests

data:
	.venv/bin/invoice-agent generate

eval:
	.venv/bin/invoice-agent eval --split test

eval-dev:
	.venv/bin/invoice-agent eval --split dev

ui:
	.venv/bin/streamlit run src/invoice_agent/ui.py

demo:
	.venv/bin/invoice-agent process data/test/test-002.pdf data/test/test-001.pdf
	.venv/bin/invoice-agent queue

docker:
	docker compose build && docker compose run --rm cli process data/test/test-001.pdf
