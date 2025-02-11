FROM mambaorg/micromamba:2.0.6-debian12-slim

COPY app/requirements.txt /tmp/requirements.txt
COPY app/app.py /opt/app.py

RUN micromamba install -y -n base -c conda-forge python=3.12 && \
    micromamba install -y -n base -c conda-forge --file /tmp/requirements.txt && \
    micromamba clean --all --yes

ENV PORT=8000

ENTRYPOINT ["/usr/local/bin/_entrypoint.sh"]
