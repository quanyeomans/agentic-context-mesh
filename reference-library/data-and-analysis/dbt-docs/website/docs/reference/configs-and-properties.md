---
title: "Configurations and properties, what are they?"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

Resources in your project—models, snapshots, seeds, tests, and the rest—can have a number of declared _properties_. Resources can also define _configurations_ (configs), which are a special kind of property that bring extra abilities. What's the distinction?
- Properties are declared for resources one-by-one in  `properties.yml` files. Configs can be defined there, nested under a `config` property. They can also be set one-by-one via a `config()` macro (right within `.sql` files), and for many resources at once in `dbt_project.yml`.
- Because configs can be set in multiple places, they are also applied hierarchically. An individual resource might _inherit_ or _override_ configs set elsewhere.
- You can select resources based on their config values using the `config:` selection method, but not the values of non-config properties.
- There are slightly different naming conventions for properties and configs depending on the file type. Refer to [naming convention](/reference/dbt_project.yml#naming-convention) for more details.

A rule of thumb: properties declare things _about_ your project resources; configs go the extra step of telling dbt _how_ to build those resources in your warehouse. This is generally true, but not always, so it's always good to check!

For example, you can use resource **properties** to:
* Describe models, snapshots, seed files, and their columns
* Assert "truths" about a model, in the form of [data tests](/docs/build/data-tests), e.g. "this `id` column is unique"
* Define official downstream uses of your data models, in the form of [exposures](/docs/build/exposures), and assert an exposure's "type"

Whereas you can use **configurations** to:
* Change how a model will be materialized (<Term id="table" />, <Term id="view" />, incremental, etc)
* Declare where a seed will be created in the database (`<database>.<schema>.<alias>`)
* Declare whether a resource should persist its descriptions as comments in the database
* Apply tags and meta to a resource
