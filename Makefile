.PHONY: deploy deploy-staging sync-on-prod fetch-nginx-config clipping-service

GIT_DIR := /opt/ckan-catalog/data.naturalcapitalproject.stanford.edu
CKAN_PROD_URL := https://data.naturalcapitalproject.stanford.edu
GCLOUD_COMMON_ARGS := --project=sdss-natcap-gef-ckan --zone=us-central1-a
RESTART_DOCKER_CMD := sudo sh -c 'cd $(GIT_DIR) && git pull && docker compose build && docker compose down && docker compose up --detach --remove-orphans'
PROD_VM_NAME := ckan-2
STAGING_VM_NAME := ckan-staging

# Building happens first, while the cluster is still up, because it can take a while.
# This way we minimize catalog downtime.
deploy:
	git log origin/master..HEAD > /dev/null || (echo "There are unpushed commits.  git push and re-run."; exit 1)
	gcloud compute ssh $(GCLOUD_COMMON_ARGS) $(PROD_VM_NAME) --command="$(RESTART_DOCKER_CMD)"

deploy-staging:
	gcloud compute ssh $(GCLOUD_COMMON_ARGS) $(STAGING_VM_NAME) --command="$(RESTART_DOCKER_CMD)"

sync-on-prod:
	SYNC_SRC_URL=$(CKAN_PROD_URL) SYNC_DST_URL=$(CKAN_PROD_URL) python api-scripts/sync-datasets.py

fetch-nginx-config:
	gcloud compute scp $(GCLOUD_COMMON_ARGS) $(PROD_VM_NAME):/etc/nginx/ckan-proxy.conf     host-nginx/etc.nginx.ckan-proxy.conf
	gcloud compute scp $(GCLOUD_COMMON_ARGS) $(PROD_VM_NAME):/etc/nginx/sites-enabled/ckan  host-nginx/etc.nginx.sites-available.ckan
	gcloud compute scp $(GCLOUD_COMMON_ARGS) $(PROD_VM_NAME):/etc/nginx/nginx.conf          host-nginx/etc.nginx.nginx.conf
	gcloud compute scp $(GCLOUD_COMMON_ARGS) $(PROD_VM_NAME):/etc/nginx/throttle-bots.conf  host-nginx/etc.nginx.throttle-bots.conf


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
	python -m gunicorn --chdir ./clipping-service/app app:app --reload
