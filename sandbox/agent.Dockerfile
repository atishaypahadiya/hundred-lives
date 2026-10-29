# The agent's computer: shell, git, python. No network beyond the hl-agents network (Gitea).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates procps less nano tree \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 --shell /bin/bash agent

COPY init.py /usr/local/lib/hl-init.py
USER agent
WORKDIR /home/agent/work
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python3", "-u", "/usr/local/lib/hl-init.py"]
