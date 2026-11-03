# upgrade-pre is the including Makefile's own. Paths here are found from this
# file's own location, so the same include works wherever the submodule sits.
DESIGN_TOOLS := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))

# Recipes in strict bash rather than make's default /bin/sh, here and in every
# Makefile that includes this one.
SHELL := bash
.SHELLFLAGS := -Eeuo pipefail -O inherit_errexit -O failglob -c

# --profile test so the design and design_test services are built here rather
# than on demand by the --build the other targets carry. preview, under a
# profile of its own, runs the same dev image as design.
build:
	$(DESIGN_TOOLS)/lib/shell-common/docker/build-compose --profile test
format:
	docker compose --profile test run --rm --build design format
lint:
	docker compose --profile test run --rm --build design_test lint
# Naming the service selects its profile.
preview:
	docker compose up --build preview
test:
	docker compose --profile test run --rm --build design_test test $(TESTS)
# One step at a time even under -j, since each needs the one before it done.
upgrade:
	$(MAKE) upgrade-pre
	$(MAKE) build
	$(MAKE) test
.PHONY: build format lint preview test upgrade upgrade-pre
