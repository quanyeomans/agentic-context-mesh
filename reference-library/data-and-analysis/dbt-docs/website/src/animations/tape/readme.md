---
title: "VHS Tape Animations"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

# VHS Tape Animations

Terminal GIF animations using [VHS](https://github.com/charmbracelet/vhs).

## Install

```bash
brew install vhs
```

## Usage

```bash
cd blog/2026-02-03-dbt-agent-skills
../generate.sh
```

This generates all `.tape` files, optimizes them, and copies to `static/img/`.

Edit `.sh` scripts to change content/timing, then re-run.

## Manual optimize

```bash
brew install gifsicle
gifsicle -O3 --lossy=80 --colors=8 input.gif -o output.gif
```
