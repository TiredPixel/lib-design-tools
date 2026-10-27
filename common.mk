# The make targets every repository using these tools shares, taken with
# `include` from its Makefile, which adds its own upgrade-pre. Paths here are
# found from this file's own location, so the same include works wherever the
# submodule sits.
DESIGN_TOOLS := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))

# --profile test so the design and design_test services are built here rather
# than on demand by the --build the other targets carry.
build:
	$(DESIGN_TOOLS)/lib/shell-common/docker/build-compose --profile test
format:
	docker compose --profile test run --rm --build design format
lint:
	docker compose --profile test run --rm --build design_test lint
test:
	docker compose --profile test run --rm --build design_test test $(TESTS)
# One step at a time even under -j, since each needs the one before it done.
upgrade:
	$(MAKE) upgrade-pre
	$(MAKE) build
	$(MAKE) test
.PHONY: build format lint test upgrade-pre upgrade
