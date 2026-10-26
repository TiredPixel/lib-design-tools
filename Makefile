DC = docker compose --profile tools run --rm --build

# --profile tools so the format and test services are built here rather than on
# demand by the --build the gates carry.
build:
	lib/shell-common/docker/build-compose --profile tools
format:
	$(DC) format
lint:
	$(DC) test lint
test:
	$(DC) test test $(TESTS)
upgrade-pre:
	cd lib/shell-common && git checkout latest && git pull
	lib/shell-common/docker/upgrade
upgrade: upgrade-pre build format test
.PHONY: build format lint test upgrade-pre upgrade
