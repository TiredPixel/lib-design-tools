# upgrade-pre is the including Makefile's own. Paths here are found from this
# file's own location, so the same include works wherever the submodule sits.
DESIGN_TOOLS := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))

# Recipes in strict bash rather than make's default /bin/sh, here and in every
# Makefile that includes this one.
SHELL := bash
.SHELLFLAGS := -Eeuo pipefail -O inherit_errexit -O failglob -c

# --profile test so the design and design_test services are built here rather
# than on demand by the --build the other targets carry. preview, under a
# profile of its own, builds the same dev stage as design on demand.
build:
	$(DESIGN_TOOLS)/lib/shell-common/docker/build-compose --profile test
# In design, whose mount carries the report to tmp/coverage/ on the host.
coverage:
	docker compose --profile test run --rm --build design coverage $(TESTS)
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
	$(MAKE) upgrade-post
# Alone, it finishes an upgrade whose build or tests failed, once fixed.
# UPGRADE_COMMIT, where the including Makefile sets it, commits what upgrade-pre
# moved.
upgrade-post:
	$(MAKE) build
	$(MAKE) test
	$(UPGRADE_COMMIT)
.PHONY: build coverage format lint preview test upgrade upgrade-post upgrade-pre
