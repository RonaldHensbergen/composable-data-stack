.PHONY: install validate validate-profile package lint lint-markdown lint-yaml lint-deprecated lint-renovate docker-build test check contract-coverage practices-audit test-mutation k3d-build k3d-up k3d-install k3d-expose k3d-e2e k3d-down tender-export tender-load tender-dashboard tender-e2e

PROFILE ?= profiles/local-dagster-postgres-superset/profile.yaml


install:
	pip install -e ".[test]"

lint: lint-markdown lint-yaml lint-deprecated lint-renovate

test:
	# Only fail repo-owned deprecations so third-party library warnings do not
	# break the suite. This must be done via warnings.filterwarnings() in
	# Python code, not the PYTHONWARNINGS env var: CPython's -W/PYTHONWARNINGS
	# parser always re.escape()s the module field, so it cannot express
	# "starts with" scoping (see scripts/run_tests_with_deprecation_gate.py).
	python scripts/run_tests_with_deprecation_gate.py

check: lint test

# Regenerate the published contract-coverage report (docs/contract-coverage.md).
# CI runs `python scripts/check_contract_coverage.py --check` and fails if the
# committed report is stale.
contract-coverage:
	python scripts/check_contract_coverage.py

# Biweekly roll-up of the adopted working practices (contract coverage,
# fix:/refine: commit mix, automerge usage, latest kill-matrix run).
# Filed as an issue by .github/workflows/practices-audit.yml.
practices-audit:
	python scripts/check_practices_audit.py

# Kill matrix (mutation testing): every test must earn its place by catching a
# mutant no other test catches. Installs mutmut ad hoc (pinned) so the
# project's own lockfile is untouched; see pyproject.toml [tool.mutmut].
test-mutation:
	python -m pip install "mutmut==3.8.1" pytest
	python -m mutmut run
	python -m mutmut results

lint-markdown:
	npx --yes markdownlint-cli@0.49.0 "**/*.md" ".github/**/*.md"

lint-yaml:
	yamllint .

lint-deprecated:
	ruff check .

lint-renovate:
	npx --yes --package renovate@44.140.0 -- renovate-config-validator --strict renovate.json

validate:
	cds validate $(PROFILE)

validate-profile:
	@if [ -z "$(P)" ]; then \
		echo "Usage: make validate-profile P=profiles/.../profile.yaml"; \
		exit 1; \
	fi
	cds validate $(P)

package:
	python3 -m pip install --upgrade build
	python3 -m build

docker-build:
	@echo "Building all Dockerfiles..."
	@for dockerfile in $$(find . -name "Dockerfile*" -type f); do \
		dir=$$(dirname "$$dockerfile"); \
		echo "Building $$dockerfile in directory $$dir..."; \
		context="$$dir"; \
		case "$$dockerfile" in \
			./images/dagster/*|./images/superset/*|./images/dbt/*|./images/dlt/*|./images/postgres/*|./images/restic/*) context="." ;; \
		esac; \
		docker build -f "$$dockerfile" "$$context" || exit 1; \
	done
	@echo "All Dockerfiles built successfully!"

k3d-build:
	scripts/k8s/build-images.sh

k3d-up:
	scripts/k8s/k3d-up.sh

k3d-install:
	scripts/k8s/install.sh $(PROFILE)

k3d-expose:
	scripts/k8s/expose-local.sh

k3d-e2e:
	scripts/k8s/e2e.sh $(PROFILE)

k3d-down:
	scripts/k8s/k3d-down.sh

tender-export:
	scripts/tender/export-snapshot.sh

tender-load:
	scripts/tender/load-snapshot.sh

tender-dashboard:
	scripts/tender/provision-dashboard.sh

tender-e2e:
	scripts/tender/e2e.sh
