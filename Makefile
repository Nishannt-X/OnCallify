# OnCallify: one entry point. `make help` lists targets.
SHELL := bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c
PY ?= python3

.PHONY: help setup build test lint up down status demo capture eval train gate validate clean

help: ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup: ## install deps and create the local stack (Postgres roles, Tomcat base, config repo)
	$(PY) -m pip install -e "agent[dev]"
	cd gateway && npm ci
	cd web && npm ci
	deploy/local/setup.sh

build: ## build the WAR, the gateway and the dashboard
	ledgerline/build.sh || (cd ledgerline && mvn -B -ntp -q package)
	cd gateway && npm run build
	cd web && npm run build

test: ## every test suite: JUnit, Jest, pytest, bats, Vitest
	ledgerline/build.sh || (cd ledgerline && mvn -B -ntp -q test)
	cd gateway && npm test
	cd agent && $(PY) -m pytest -q
	bats runbooks/tests/runbooks.bats
	cd web && npm test

lint: ## static checks: javac -Werror (in build.sh), tsc, ruff, mypy --strict, shellcheck
	cd gateway && npm run lint
	cd web && npm run typecheck
	cd agent && ruff check src tests && $(PY) -m mypy
	shellcheck -x -P SCRIPTDIR runbooks/linux/*.sh deploy/local/*.sh ledgerline/build.sh

up: ## start Tomcat+Ledgerline, gateway, agent (dashboard on :7080)
	deploy/local/stack.sh up

down: ## stop everything
	deploy/local/stack.sh down

status:
	deploy/local/stack.sh status

demo: ## two nights: a human approves the fix, then a contract handles the repeat
	@test -n "$${TOKEN:-}" || { echo "usage: make demo TOKEN=<approver token>  (create: oncallify approver --name you)"; exit 2; }
	$(PY) scripts/e2e_demo.py --approver-token "$$TOKEN"

capture: ## break the live stack 56 ways and record the replay corpus (stop the agent first)
	ONCALLIFY_LEDGER_IGNORE_AUTHORS=scenario-reset $(PY) -m oncallify.cli eval capture

train: ## train the ml-logreg diagnoser on the corpus
	$(PY) -m oncallify.cli eval train

eval: ## paired replay of all offline variants + report (eval/reports/report.md)
	$(PY) -m oncallify.cli eval run --variants rules-v1,rules-v2,ml-logreg

eval-llm: ## same, adding Granite arms (needs Ollama with a Granite model pulled)
	$(PY) -m oncallify.cli eval run --variants rules-v2,ml-logreg,granite,granite-rag --repeats 3

gate: ## CI gate: candidate must be non-inferior to the baseline
	$(PY) -m oncallify.cli eval gate --baseline rules-v2 --candidate $${CANDIDATE:-ml-logreg}

validate: ## A/A simulation of the statistics
	$(PY) -m oncallify.cli eval validate

clean:
	rm -rf ledgerline/target gateway/dist web/dist .run
