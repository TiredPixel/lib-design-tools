# The make targets every repository using these tools shares, taken with
# `include` from its Makefile, which adds its own upgrade-pre. Paths here are
# found from this file's own location, so the same include works wherever the
# submodule sits.
DESIGN_TOOLS := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))

DC = docker compose --profile tools run --rm --build

# --profile tools so the format and test services are built here rather than on
# demand by the --build the gates carry.
build:
	$(DESIGN_TOOLS)/lib/shell-common/docker/build-compose --profile tools
format:
	$(DC) format
lint:
	$(DC) test lint
test:
	$(DC) test test $(TESTS)
# One step at a time even under -j, since each needs the one before it done.
upgrade:
	$(MAKE) upgrade-pre
	$(MAKE) build
	$(MAKE) format
	$(MAKE) test
.PHONY: build format lint test upgrade-pre upgrade
