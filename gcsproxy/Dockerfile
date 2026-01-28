# syntax=docker/dockerfile:1

FROM debian:13.3 as build

# everything needed to build our go project from source
RUN apt-get update && apt-get install -y golang-go golang-src git build-essential

# Rev is the latest on the implement-range-requests branch as of 2026-01-28
RUN git clone -b 290e11b6e9ebe6ab2f302e8423b6d51652aff10e https://github.com/phargogh/gcsproxy /tmp/gcsproxy \
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
