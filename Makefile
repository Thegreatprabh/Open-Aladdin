# OPEN-ALADDIN — top-level build orchestration.
#
#   make build        Build every component (Rust engine, Python env, UI)
#   make test         Run the Rust matching-engine test suite
#   make engine       Build the Rust matching engine (release)
#   make quant        Install the Python quant-engine dependencies
#   make ui           Install and build the terminal UI
#   make generate-50k Generate the 50,000+ line monorepo expansion
#   make docker-up    Bring up the full stack with Docker Compose
#   make clean        Remove build artifacts

CARGO_DIR  := core/matching-engine
QUANT_DIR  := quant-engine
UI_DIR     := apps/terminal-ui

.PHONY: build test engine quant ui generate-50k docker-up clean

build: engine quant ui

test:
	cd $(CARGO_DIR) && cargo test

engine:
	cd $(CARGO_DIR) && cargo build --release

quant:
	pip install -r $(QUANT_DIR)/requirements.txt

ui:
	cd $(UI_DIR) && npm install && npm run build

generate-50k:
	python3 scripts/generate_50k_monorepo.py

docker-up:
	docker compose up --build

clean:
	cd $(CARGO_DIR) && cargo clean
	rm -rf $(UI_DIR)/dist $(UI_DIR)/node_modules generated
