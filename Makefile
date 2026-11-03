include common.mk

UPGRADE_COMMIT = lib/shell-common/docker/upgrade-commit tmp/upgrade.record lib/shell-common

upgrade-pre:
	git submodule sync lib/shell-common
	git submodule update --init --checkout lib/shell-common
	cd lib/shell-common && git checkout latest && git pull --ff-only
	lib/shell-common/docker/upgrade --record tmp/upgrade.record Dockerfile .github/workflows/*.yml
# On the host rather than in the image, since it asks on the terminal and
# pushes.
workflows-approve:
	lib/shell-common/github/workflows-approve
.PHONY: workflows-approve
