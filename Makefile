.PHONY: install validate validate-profile package lint lint-markdown lint-yaml lint-deprecated lint-renovate docker-build test check k3d-build k3d-up k3d-install k3d-expose k3d-e2e k3d-down tender-export tender-load

PROFILE ?= profiles/local-dagster-postgres-superset/profile.yaml


install:
	pip install -e .

lint: lint-markdown lint-yaml lint-deprecated lint-renovate

test:
	# Only fail repo-owned deprecations so third-party library warnings do not
	# break the suite. This must be done via warnings.filterwarnings() in
	# Python code, not the PYTHONWARNINGS env var: CPython's -W/PYTHONWARNINGS
	# parser always re.escape()s the module field, so it cannot express
	# "starts with" scoping (see scripts/run_tests_with_deprecation_gate.py).
	python scripts/run_tests_with_deprecation_gate.py

check: lint test

lint-markdown:
	npx --yes markdownlint-cli@0.49.0 "**/*.md" ".github/**/*.md"

lint-yaml:
	yamllint .

lint-deprecated:
	ruff check .

lint-renovate:
	npx --yes --package renovate -- renovate-config-validator --strict renovate.json

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
			./images/dagster/*|./images/superset/*) context="." ;; \
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
