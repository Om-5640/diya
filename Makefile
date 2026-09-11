.PHONY: install mock api test all

install:
	pip install -r requirements.txt

mock:
	python -m core.mock

api:
	uvicorn api.main:app --reload --port 8000

test:
	pytest -q

all: install mock test
