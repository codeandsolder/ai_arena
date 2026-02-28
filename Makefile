.PHONY: test

test:
	pytest tests/ -v --cov=backend --cov-report=term-missing --cov-report=xml

