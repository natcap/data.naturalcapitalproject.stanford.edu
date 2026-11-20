.PHONY: deploy deploy-staging sync-on-prod fetch-nginx-config clipping-service env-clip ckan-dev tileserver fetch-state-from-prod

GIT_DIR := /opt/ckan-catalog/data.naturalcapitalproject.stanford.edu
CKAN_PROD_URL := https://data.naturalcapitalproject.stanford.edu
GCLOUD_LOCATION := --project=sdss-natcap-gef-ckan --zone=us-central1-a
RESTART_DOCKER_CMD := sudo sh -c 'cd $(GIT_DIR) && git pull && docker compose build && docker compose down && docker compose up --detach --remove-orphans'
STOP_DOCKER_COMMAND := sudo sh -c 'cd $(GIT_DIR) && git pull && docker compose down'
START_DOCKER_COMMAND := sudo sh -c 'cd $(GIT_DIR) && docker compose up --detach --remove-orphans'
PROD_VM_NAME := ckan-3
STAGING_VM_NAME := ckan-staging
DOCKER_VOLUMES := /var/lib/docker/volumes
DOCKER_VOL_CKAN := $(DOCKER_VOLUMES)/datanaturalcapitalprojectstanfordedu_ckan_storage
DOCKER_VOL_POSTGRES := $(DOCKER_VOLUMES)/datanaturalcapitalprojectstanfordedu_pg_data

# Building happens first, while the cluster is still up, because it can take a while.
# This way we minimize catalog downtime.
deploy:
	git log origin/master..HEAD > /dev/null || (echo "There are unpushed commits.  git push and re-run."; exit 1)
	gcloud compute ssh $(PROD_VM_NAME) --command="$(RESTART_DOCKER_CMD)" $(GCLOUD_LOCATION)

deploy-staging:
	gcloud compute ssh $(STAGING_VM_NAME) --command="$(RESTART_DOCKER_CMD)" $(GCLOUD_LOCATION)

sync-on-prod:
	SYNC_SRC_URL=$(CKAN_PROD_URL) SYNC_DST_URL=$(CKAN_PROD_URL) python api-scripts/sync-datasets.py

fetch-nginx-config:
	gcloud compute scp $(PROD_VM_NAME):/etc/nginx/ckan-proxy.conf     host-nginx/etc.nginx.ckan-proxy.conf $(GCLOUD_LOCATION)
	gcloud compute scp $(PROD_VM_NAME):/etc/nginx/sites-enabled/ckan  host-nginx/etc.nginx.sites-available.ckan $(GCLOUD_LOCATION)
	gcloud compute scp $(PROD_VM_NAME):/etc/nginx/nginx.conf          host-nginx/etc.nginx.nginx.conf $(GCLOUD_LOCATION)
	gcloud compute scp $(PROD_VM_NAME):/etc/nginx/throttle-bots.conf  host-nginx/etc.nginx.throttle-bots.conf $(GCLOUD_LOCATION)

fetch-state-from-prod:
	-gcloud compute ssh $(PROD_VM_NAME) --command="$(STOP_DOCKER_COMMAND)" $(GCLOUD_LOCATION)
	gcloud compute ssh $(PROD_VM_NAME) --command="sudo sh -c 'sudo zip -r ckan-state.zip $(DOCKER_VOL_CKAN) $(DOCKER_VOL_POSTGRES)'" $(GCLOUD_LOCATION)
	gcloud compute ssh $(PROD_VM_NAME) --command="$(START_DOCKER_COMMAND)" $(GCLOUD_LOCATION)
	gcloud compute scp "$(PROD_VM_NAME):~/ckan-state.zip" . $(GCLOUD_LOCATION)

# Targets for local development:
CLIP_ENV := clipenv

env-clip:
	conda create -n $(CLIP_ENV) -y -c conda-forge python=3.12
	conda env update -n $(CLIP_ENV) --file ./clipping-service/app/requirements.txt
	conda env config vars set -n $(CLIP_ENV) DEV_MODE=True
	@echo "To activate the environment and run the clipping service:"
	@echo ">> conda activate $(CLIP_ENV)"
	@echo ">> make clipping-service"

clipping-service:
	python -m gunicorn --chdir ./clipping-service/app app:app --timeout 180 --reload

tileserver:
	python -m gunicorn --chdir ./tileserver/app main:app --timeout 180 --reload

ckan-dev:
	docker compose -f docker-compose.dev.yml build
	docker compose -f docker-compose.dev.yml up --watch
