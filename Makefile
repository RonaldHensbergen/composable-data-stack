l.PHONY: install validate validate-profile

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
