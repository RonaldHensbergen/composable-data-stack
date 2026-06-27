.PHONY: install validate validate-profile package check

PROFILE ?= profiles/local-dagster-postgres-superset/profile.yaml

install:
	pip install -e .

check:
	python3 -m unittest discover -s tests -p "*.py"
	yamllint .
	npx --yes markdownlint-cli@0.39.0 "**/*.md" ".github/**/*.md"

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
