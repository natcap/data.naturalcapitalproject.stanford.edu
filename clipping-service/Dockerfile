FROM mambaorg/micromamba:2.0.6-debian12-slim

COPY app/requirements.txt /tmp/requirements.txt
COPY app/app.py /opt/app.py

RUN micromamba install -y -n base -c conda-forge python=3.12 && \
    micromamba install -y -n base -c conda-forge --file /tmp/requirements.txt && \
    micromamba clean --all --yes

ENV PORT=8000
ENV HOST=0.0.0.0
ENV WORKSPACE_DIR=/tmp
ENV PROJ_LIB=/opt/conda/share/proj
ENV PROJ_DATA=/opt/conda/share/proj
WORKDIR /opt

ENTRYPOINT ["/opt/conda/bin/python", "-m", "gunicorn", "app:app"]
