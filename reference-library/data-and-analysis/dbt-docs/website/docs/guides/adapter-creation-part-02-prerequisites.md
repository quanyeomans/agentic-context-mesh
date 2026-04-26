## Prerequisites

It is very important that you have the right skills, and understand the level of difficulty required to make an adapter for your data platform.

The more you can answer Yes to the below questions, the easier your adapter development (and user-) experience will be. See the [New Adapter Information Sheet wiki](https://github.com/dbt-labs/dbt-core/wiki/New-Adapter-Information-Sheet) for even more specific questions.

### Training

- The developer (and any product managers) ideally will have substantial experience as an end-user of dbt. If not, it is highly advised that you at least take the [dbt Fundamentals](https://learn.getdbt.com/courses/dbt-fundamentals) and [Advanced Materializations](https://learn.getdbt.com/courses/advanced-materializations) course.

### Database

- Does the database complete transactions fast enough for interactive development?
- Can you execute SQL against the data platform?
- Is there a concept of schemas?
- Does the data platform support ANSI SQL, or at least a subset?

### Driver / Connection Library

- Is there a Python-based driver for interacting with the database that is db API 2.0 compliant (e.g. Psycopg2 for Postgres, pyodbc for SQL Server)
- Does it support: prepared statements, multiple statements, or single sign on token authorization to the data platform?

### Open source software

- Does your organization have an established process for publishing open source software?

It is easiest to build an adapter for dbt when the following the <Term id="data-warehouse" />/platform in question has:

- a conventional ANSI-SQL interface (or as close to it as possible),
- a mature connection library/SDK that uses ODBC or Python DB 2 API, and
- a way to enable developers to iterate rapidly with both quick reads and writes

### Maintaining your new adapter

When your adapter becomes more popular, and people start using it, you may quickly become the maintainer of an increasingly popular open source project. With this new role, comes some unexpected responsibilities that not only include code maintenance, but also working with a community of users and contributors. To help people understand what to expect of your project, you should communicate your intentions early and often in your adapter documentation or README. Answer questions like, Is this experimental work that people should use at their own risk? Or is this production-grade code that you're committed to maintaining into the future?

#### Keeping the code compatible with dbt Core

An adapter is compatible with dbt Core if it has correctly implemented the interface defined in [dbt-adapters](https://github.com/dbt-labs/dbt-adapters/) and is tested by [dbt-tests-adapters](https://github.com/dbt-labs/dbt-adapters/tree/main/dbt-tests-adapter). Prior to dbt Core version 1.8, this interface was contained in `dbt-core`. 

New minor version releases of `dbt-adapters` may include changes to the Python interface for adapter plugins, as well as new or updated test cases. The maintainers of `dbt-adapters` will clearly communicate these changes in documentation and release notes, and they will aim for backwards compatibility whenever possible.

Patch releases of `dbt-adapters` will _not_ include breaking changes or new features to adapter-facing code.

#### Versioning and releasing your adapter

dbt Labs strongly recommends you to adopt the following approach when versioning and releasing your plugin. 

- Declare major version compatibility with `dbt-adapters` and only set a boundary on the minor version if there is some known reason.
- Do not import or rely on code from `dbt-core`. 
- Aim to release a new minor version of your plugin as you add substantial new features. Typically, this will be triggered by adding support for new features released in `dbt-adapters` or by changes to the data platform itself.
- While your plugin is new and you're iterating on features, aim to offer backwards compatibility and deprecation notices for at least one minor version. As your plugin matures, aim to leave backwards compatibility and deprecation notices in place until the next major version (<Constant name="core" /> v2).
- Release patch versions of your plugins whenever needed. These patch releases should only contain fixes.

:::note

Prior to dbt Core version 1.8, we recommended that the minor version of your plugin should match the minor version in `dbt-core` (for example, 1.1.x).

:::