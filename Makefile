.PHONY: install validate validate-profile package lint lint-markdown lint-yaml

PROFILE ?= profiles/local-dagster-postgres-superset/profile.yaml

install:
	pip install -e .

lint: lint-markdown lint-yaml

lint-markdown:
	npx --yes markdownlint-cli@0.39.0 "**/*.md" ".github/**/*.md"

lint-yaml:
	yamllint .

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
