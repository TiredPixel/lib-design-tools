include design.mk

upgrade-pre:
	cd lib/shell-common && git checkout latest && git pull
	lib/shell-common/docker/upgrade

# On the host, since it drives Docker itself.
example:
	bin/example-boot
.PHONY: example
