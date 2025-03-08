# Define the default action
.PHONY: help
help:
	@echo "Makefile commands:"
	@echo "  make build    - Build the Docker containers"
	@echo "  make up       - Build and start the containers"
	@echo "  make down     - Stop and remove the containers"

# Build the Docker images
.PHONY: build
build:
	docker-compose -f docker/docker-compose.yml build

# Start the containers (builds them if needed)
.PHONY: up
up:
	docker-compose -f docker/docker-compose.yml up

# Stop and remove the containers
.PHONY: down
down:
	docker-compose -f docker/docker-compose.yml down