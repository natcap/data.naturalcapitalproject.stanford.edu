FROM mambaorg/micromamba:2.0.6-debian12-slim

COPY app/requirements.txt /tmp/requirements.txt

RUN micromamba install -y -n base -c conda-forge python=3.12 && \
    micromamba install -y -n base -c conda-forge --file /tmp/requirements.txt && \
    micromamba clean --all --yes

COPY app/app.py /opt/app.py
COPY app/templates /opt/templates
COPY app/static /opt/static

ENV PORT=8000
ENV HOST=0.0.0.0
ENV WORKSPACE_DIR=/tmp
ENV PROJ_LIB=/opt/conda/share/proj
ENV PROJ_DATA=/opt/conda/share/proj
ENV GUNICORN_CMD_ARGS="--timeout=300"
WORKDIR /opt

ENTRYPOINT ["/opt/conda/bin/python", "-m", "gunicorn", "app:app"]
