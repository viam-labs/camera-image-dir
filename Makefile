.PHONY: setup module clean

setup:
	./setup.sh

module:
	./build.sh

clean:
	rm -rf venv dist build .pkg *.spec __pycache__ .pytest_cache