include common.mk

UPGRADE_COMMIT = lib/shell-common/docker/upgrade-commit tmp/upgrade.record lib/shell-common

upgrade-pre:
	git submodule sync lib/shell-common
	git submodule update --init --checkout lib/shell-common
	lib/shell-common/github/workflows-pending
	cd lib/shell-common && git checkout latest && git pull --ff-only
	lib/shell-common/docker/upgrade --record tmp/upgrade.record Dockerfile .github/workflows/*.yml
