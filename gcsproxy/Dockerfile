# syntax=docker/dockerfile:1

FROM debian:13.3 as build

# everything needed to build our go project from source
RUN apt-get update && apt-get install -y golang-go golang-src git build-essential
RUN git clone https://github.com/phargogh/gcsproxy /tmp/gcsproxy \
        && cd /tmp/gcsproxy \
        && make build \
        && /tmp/gcsproxy/bin/gcsproxy --help

# build on debian, then install just the gcsproxy binary
# Debian 13 is codenamed "trixie"
FROM nginx:mainline-trixie
COPY --from=build /tmp/gcsproxy/bin/gcsproxy /usr/sbin

COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /tmp/

ENTRYPOINT /tmp/entrypoint.sh
