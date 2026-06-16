.PHONY: dev build deploy service clean

dev:  # Run dev with hot reload (docker compose, source mounted, single subscribe source)
	docker compose -f docker-compose.dev.yml up --build

build:  # Build Docker image
	docker build -t iptv-api .

deploy:  # Deploy with docker compose (production)
	docker compose up -d --build

service:  # Start web service locally
	pipenv run python service/app.py

clean:  # Clean output data
	@rm -rf output/data output/epg output/log output/*.log
	@echo "Cleaned output data"
