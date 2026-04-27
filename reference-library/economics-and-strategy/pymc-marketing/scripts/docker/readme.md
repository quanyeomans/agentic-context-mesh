---
title: "Docker Image"
source: PyMC-Marketing (Bayesian MMM)
source_url: https://github.com/pymc-labs/pymc-marketing
licence: Apache-2.0
domain: economics-and-strategy
subdomain: pymc-marketing
date_added: 2026-04-25
---

# Docker Image

To build the docker image, run the following command:

```bash
cd scripts/docker/
docker build -t docker-pymc-marketing .
```

To run the docker image, run the following command:

```bash
cd /path/to/your/project
docker run -it -p 8888:8888 -v $(pwd):/home/jovyan/work docker-pymc-marketing
```

Now you are ready to access the Jupyter Notebook:

> Visiting http://<hostname>:8888/?token=<token> in a browser loads the Jupyter Notebook dashboard page, where hostname is the name of the computer running docker and token is the secret token printed in the console.
