FROM ckan/ckan-base:2.11.1

ARG GITHUB_USER
ARG GITHUB_REPO
ARG GITHUB_BRANCH

USER root

# Set up environment variables
ENV APP_DIR=/opt/ckan-catalog/data.naturalcapitalproject.stanford.edu
ENV TZ=UTC
RUN echo ${TZ} > /etc/timezone

# Make sure both files are not exactly the same
RUN if ! [ /usr/share/zoneinfo/${TZ} -ef /etc/localtime ]; then \
        cp /usr/share/zoneinfo/${TZ} /etc/localtime ;\
    fi ;

# Install package dependencies for ckanext-spatial
ENV PROJ_DIR=/usr
ENV PROJ_LIBDIR=/usr/lib
ENV PROJ_INCDIR=/usr/include
# Using latest commit from master branch to avoid having to compile PROJ from source
ENV CKANEXT_SPATIAL_GIT_REVISION=v2.2.0
RUN apt-get update && apt-get install -y libgeos-dev libgeos3.11.1 libproj-dev proj-bin python3-shapely
RUN pip3 install -e git+https://github.com/ckan/ckanext-spatial.git@$CKANEXT_SPATIAL_GIT_REVISION#egg=ckanext-spatial && \
    pip3 install -r https://raw.githubusercontent.com/ckan/ckanext-spatial/refs/tags/$CKANEXT_SPATIAL_GIT_REVISION/requirements.txt
RUN pip3 install -e 'git+https://github.com/ckan/ckanext-scheming.git@3af6056bbe16c3f8c6257f18cb2a0805370f85de#egg=ckanext-scheming'
RUN pip3 install "numpy<2"

ENV CKANEXT_GOOGLEANALYTICS_GIT_REV=6484a04fdc5eca1cdee9e3ee3b4e881678d1538d
RUN pip3 install -e git+https://github.com/ckan/ckanext-googleanalytics.git@$CKANEXT_GOOGLEANALYTICS_GIT_REV#egg=ckanext-googleanalytics && \
    pip3 install -r https://raw.githubusercontent.com/ckan/ckanext-googleanalytics/$CKANEXT_GOOGLEANALYTICS_GIT_REV/requirements.txt && \
    python3 -m pip install oauth2client

ENV CKANEXT_ORFACET_GIT_REV=7a01391186761ff112a76bc7e62dd487330a2371
RUN pip3 install -e git+https://github.com/DataShades/ckanext-or_facet.git@$CKANEXT_ORFACET_GIT_REV#egg=ckanext-or_facet

# NOTE: requires that the build context includes ./src/
COPY ./src/ckanext-natcap /srv/app/src/ckanext-natcap
RUN pip3 install -r /srv/app/src/ckanext-natcap/requirements.txt && \
    pip3 install -e /srv/app/src/ckanext-natcap

COPY ./src/ckanext-mappreview /srv/app/src/ckanext-mappreview
RUN pip3 install -r /srv/app/src/ckanext-mappreview/requirements.txt && \
    pip3 install -e /srv/app/src/ckanext-mappreview

COPY ./src/ckanext-zipexpand /srv/app/src/ckanext-zipexpand
RUN pip3 install -r /srv/app/src/ckanext-zipexpand/requirements.txt && \
    pip3 install -e /srv/app/src/ckanext-zipexpand

# Install any extensions needed by your CKAN instance
# See Dockerfile.dev for more details and examples

# Copy custom initialization scripts
COPY ./ckan/docker-entrypoint.d/* /docker-entrypoint.d/

# Apply any patches needed to CKAN core or any of the built extensions (not the
# runtime mounted ones)
COPY ./ckan/patches ${APP_DIR}/patches

RUN for d in $APP_DIR/patches/*; do \
        if [ -d $d ]; then \
            for f in `ls $d/*.patch | sort -g`; do \
                cd $SRC_DIR/`basename "$d"` && echo "$0: Applying patch $f to $SRC_DIR/`basename $d`"; patch -p1 < "$f" ; \
            done ; \
        fi ; \
    done
