.PHONY: install validate validate-profile package lint lint-markdown lint-yaml docker-build

PROFILE ?= profiles/local-dagster-postgres-superset/profile.yaml

install:
	pip install -e .

lint: lint-markdown lint-yaml

lint-markdown:
	npx --yes markdownlint-cli@0.49.0 "**/*.md" ".github/**/*.md"

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

docker-build:
	@echo "Building all Dockerfiles..."
	@for dockerfile in $$(find . -name "Dockerfile*" -type f); do \
		dir=$$(dirname "$$dockerfile"); \
		echo "Building $$dockerfile in directory $$dir..."; \
		context="$$dir"; \
		if [ "$$dockerfile" = "./images/dagster/Dockerfile.user-code" ]; then \
			context="."; \
		fi; \
		docker build -f "$$dockerfile" "$$context" || exit 1; \
	done
	@echo "All Dockerfiles built successfully!"
