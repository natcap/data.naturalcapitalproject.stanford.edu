#!/usr/bin/env sh
#
# Install all nginx configuration into the correct place.

cp -v ./etc.nginx.ckan-proxy.conf /etc/nginx/ckan-proxy.conf
cp -v ./etc.nginx.nginx.conf /etc/nginx/nginx.conf
cp -v ./etc.nginx.sites-available.ckan /etc/nginx/sites-available/ckan
cp -v ./etc.nginx.throttle-bots.conf /etc/nginx/throttle-bots.conf

ln -s /etc/nginx/sites-available/ckan /etc/nginx/sites-enabled/ckan


