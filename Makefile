l.PHONY: install validate validate-profile package

PROFILE ?= profiles/local-dagster-postgres-superset/profile.yaml

install:
	pip install -e .

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
