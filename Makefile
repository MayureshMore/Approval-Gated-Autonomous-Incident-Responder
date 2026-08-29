# Convenience targets. Everything here is a thin wrapper over commands that also
# work standalone — nothing in the project depends on make.

PYTHON ?= .venv/bin/python

.PHONY: help setup test selftest demo demo-live demo-resume reset runs clean

help:
	@echo "make setup       create .venv and install dependencies"
	@echo "make test        run the test suite"
	@echo "make selftest    verify wiring without running the agent"
	@echo "make demo        full demo, sim provider (no API key needed)"
	@echo "make demo-live   full demo on the TrueFoundry gateway"
	@echo "make demo-resume continue a killed run (Layer 4 beat)"
	@echo "make reset       re-arm the scenario between runs"
	@echo "make runs        list saved run checkpoints"

setup:
	uv venv --python 3.12 .venv && uv pip install -r requirements.txt

test:
	$(PYTHON) -m pytest

selftest:
	$(PYTHON) run_agent.py --selftest

demo:
	scripts/demo.sh --provider sim --subagents

demo-live:
	scripts/demo.sh --provider truefoundry --subagents

demo-resume:
	scripts/demo.sh --provider sim --resume last

reset:
	@curl -sf -X POST localhost:8000/reset >/dev/null && echo "mock_env reset"
	@curl -sf -X POST localhost:8500/reset >/dev/null && echo "bus reset"

runs:
	$(PYTHON) run_agent.py --list-runs

clean:
	rm -rf runs .pytest_cache ui/events.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
