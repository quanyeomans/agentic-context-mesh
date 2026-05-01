---
title: "validate the JSON schema with `opis/json-schema`"
source: CycloneDX SBOM Specification
source_url: https://github.com/CycloneDX/specification
licence: Apache-2.0
domain: security
subdomain: cyclonedx-spec
date_added: 2026-04-25
---

# validate the JSON schema with `opis/json-schema`

uses https://opis.io/json-schema/2.x/php-loader.html
for validation of a schema

## requirements

* php >= 7.4
* php composer

## setup

```shell
composer update
```

## usage

```shell
composer run test
```
