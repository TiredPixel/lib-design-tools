# Paths here are found from this file's own location, so the same include works
# wherever the submodule sits.
DESIGN_TOOLS := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))

UPGRADE_PIN_ARGS ?= --workflows .github/workflows/*.yml
UPGRADE_STEPS ?=
UPGRADE_SUBMODULES ?= lib/design-tools

SHELL := bash
.SHELLFLAGS := -Eeuo pipefail -O inherit_errexit -O failglob -c

# Each bin/ script is named by path, so one missing, as a link is in a clone
# without its submodules, fails at exec rather than PATH finding another command
# of that name, such as coreutils' test, which passes having run nothing.

build:
	$(DESIGN_TOOLS)/lib/shell-common/docker/build-compose --profile test
# Built first, since run --build would print its progress into the tar on
# stdout; -T, so no terminal mangles the stream.
coverage:
	docker compose --profile test build tooling_test
	docker compose --profile test run --rm -T tooling_test bin/coverage $(TESTS) \
		| $(DESIGN_TOOLS)/lib/shell-common/runner/coverage --extract
format:
	$(DESIGN_TOOLS)/lib/shell-common/runner/format-host --build -- tooling bin/format
lint:
	docker compose --profile test run --rm --build -T tooling_test bin/lint
preview:
	docker compose --profile preview up --build preview
test:
	docker compose --profile test run --rm --build -T tooling_test bin/test $(TESTS)
upgrade-pre:
	git submodule sync $(UPGRADE_SUBMODULES)
	git submodule update --init --checkout --recursive $(UPGRADE_SUBMODULES)
	$(DESIGN_TOOLS)/lib/shell-common/docker/upgrade-guard tmp/upgrade.record
	$(DESIGN_TOOLS)/lib/shell-common/project/submodule-upgrade $(UPGRADE_SUBMODULES)
	$(DESIGN_TOOLS)/lib/shell-common/docker/upgrade --record tmp/upgrade.record $(UPGRADE_PIN_ARGS)
# One step at a time even under -j, since each needs the one before it done.
upgrade:
	$(MAKE) upgrade-pre
	for step in $(UPGRADE_STEPS); do $(MAKE) "$$step"; done
	$(MAKE) upgrade-post
upgrade-post:
	$(MAKE) build
	$(MAKE) format
	$(MAKE) test
	$(DESIGN_TOOLS)/lib/shell-common/docker/upgrade-commit tmp/upgrade.record .
upgrade-discard:
	$(DESIGN_TOOLS)/lib/shell-common/docker/upgrade-discard tmp/upgrade.record $(UPGRADE_SUBMODULES)
upgrade-fonts:
	docker compose --profile test build tooling_upgrade
	$(DESIGN_TOOLS)/lib/shell-common/runner/format-host -T -e GH_TOKEN -e GITHUB_TOKEN \
		-- tooling_upgrade $(DESIGN_TOOLS)/tools/fonts-upgrade src/fonts \
		| $(DESIGN_TOOLS)/lib/shell-common/docker/upgrade-record tmp/upgrade.record src/fonts
# On the host rather than in the image, since it asks on the terminal and
# pushes.
workflows-approve:
	$(DESIGN_TOOLS)/lib/shell-common/github/workflows-approve
.PHONY: build coverage format lint preview test upgrade-pre upgrade upgrade-post upgrade-discard workflows-approve upgrade-fonts

# After every other target, so that build stays the default goal.
ifdef DOTX
UPGRADE_STEPS += dotx
# Built first, since run --build writes its progress to stdout. Through stdout,
# since the test service mounts nothing, and through a partial file, since a
# redirect into the template would empty it before a failed build.
dotx:
	docker compose --profile test build tooling_test
	mkdir -p tmp
	docker compose --profile test run --rm -T tooling_test bin/build-dotx > tmp/dotx.partial
	mv tmp/dotx.partial '$(DOTX)'
.PHONY: dotx
endif
