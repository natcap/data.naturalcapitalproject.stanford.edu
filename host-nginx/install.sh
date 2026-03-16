#!/usr/bin/env sh
#
# Install all nginx configuration into the correct place.

set -ex

cp -v ./etc.nginx.ckan-proxy.conf /etc/nginx/ckan-proxy.conf
cp -v ./etc.nginx.nginx.conf /etc/nginx/nginx.conf
cp -v ./etc.nginx.sites-available.ckan /etc/nginx/sites-available/ckan
cp -v ./etc.nginx.throttle-bots.conf /etc/nginx/throttle-bots.conf

ln -s /etc/nginx/sites-available/ckan /etc/nginx/sites-enabled/ckan || echo "sites-enabled/ckan link already in place"

# If the default site is linked into sites-available, we'll just get the default nginx page.
# Remove it to allow CKAN to be served.
rm -f /etc/nginx/sites-available/default || echo "sites-enabled/default was not enabled"


