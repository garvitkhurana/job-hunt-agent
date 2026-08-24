.PHONY: test metrics board help

help:
	@echo "hunt daily | hunt ui | hunt metrics | hunt prep <id> | hunt outcome <id> interview"

test:
	pytest -q

metrics:
	hunt metrics --baseline

board:
	hunt board
