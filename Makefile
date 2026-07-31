.PHONY: install validate validate-profile package lint lint-markdown lint-yaml lint-deprecated lint-renovate docker-build test check

PROFILE ?= profiles/local-dagster-postgres-superset/profile.yaml
PYTHON_DEPRECATION_WARNINGS ?= error::DeprecationWarning:cli,error::DeprecationWarning:test_.*


install:
	pip install -e .

lint: lint-markdown lint-yaml lint-deprecated lint-renovate

test:
	# Only fail repo-owned deprecations so third-party library warnings do not break the suite.
	PYTHONWARNINGS="$(PYTHON_DEPRECATION_WARNINGS)" python -m unittest discover -s tests -p "test_*.py" -v

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
		if [ "$$dockerfile" = "./images/dagster/Dockerfile" ] || [ "$$dockerfile" = "./images/dagster/Dockerfile.user-code" ] || [ "$$dockerfile" = "./images/superset/Dockerfile" ]; then \
			context="."; \
		fi; \
		docker build -f "$$dockerfile" "$$context" || exit 1; \
	done
	@echo "All Dockerfiles built successfully!"
