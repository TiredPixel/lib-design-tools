include design.mk

upgrade-pre:
	cd lib/shell-common && git checkout latest && git pull
	lib/shell-common/docker/upgrade
