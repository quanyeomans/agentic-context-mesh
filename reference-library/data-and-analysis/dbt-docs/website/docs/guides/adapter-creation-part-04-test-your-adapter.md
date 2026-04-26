## Test your adapter

This document has two sections:

1. Refer to "About the testing framework" for a description of the standard framework that we maintain for using pytest together with dbt. It includes an example that shows the anatomy of a simple test case.
2. Refer to "Testing your adapter" for a step-by-step guide for using our out-of-the-box suite of "basic" tests, which will validate that your adapter meets a baseline of dbt functionality.

### Testing prerequisites

- Your adapter must be compatible with <Constant name="core" /> **v1.1** or newer
- You should be familiar with **pytest**: [https://docs.pytest.org](https://docs.pytest.org)

### About the testing framework

[dbt-adapters-tests](https://github.com/dbt-labs/dbt-adapters/tree/main/dbt-tests-adapter) offers a standard framework for running prebuilt functional tests, and for defining your own tests. The core testing framework is built using `pytest`, a mature and standard library for testing Python projects.

It includes basic utilities for setting up pytest + dbt. These are used by all "prebuilt" functional tests, and make it possible to quickly write your own tests.

Those utilities allow you to do three basic things:

1. **Quickly set up a dbt "project."** Define project resources via methods such as `models()` and `seeds()`. Use `project_config_update()` to pass configurations into `dbt_project.yml`.
2. **Define a sequence of dbt commands.** The most important utility  is `run_dbt()`, which returns the [results](/reference/dbt-classes#result-objects) of each dbt command. It takes a list of CLI specifiers (subcommand + flags), as well as an optional second argument, `expect_pass=False`, for cases where you expect the command to fail.
3. **Validate the results of those dbt commands.** For example, `check_relations_equal()` asserts that two database objects have the same structure and content. You can also write your own `assert` statements, by inspecting the results of a dbt command, or querying arbitrary database objects with `project.run_sql()`.

You can see the full suite of utilities, with arguments and annotations, in [`util.py`](https://github.com/dbt-labs/dbt-core/blob/main/core/dbt/tests/util.py). You'll also see them crop up across a number of test cases. While all utilities are intended to be reusable, you won't need all of them for every test. In the example below, we'll show a simple test case that uses only a few utilities.

#### Example: a simple test case

This example will show you the anatomy of a test case using dbt + pytest. We will create reusable components, combine them to form a dbt "project", and define a sequence of dbt commands. Then, we'll use Python `assert` statements to ensure those commands succeed (or fail) as we expect.

In ["Getting started running basic tests,"](#getting-started-running-basic-tests) we'll offer step-by-step instructions for installing and configuring `pytest`, so that you can run it on your own machine. For now, it's more important to see how the pieces of a test case fit together.

This example includes a seed, a model, and two tests—one of which will fail.

1. Define Python strings that will represent the file contents in your dbt project. Defining these in a separate file enables you to reuse the same components across different test cases. The pytest name for this type of reusable component is "fixture."

<File name="tests/functional/example/fixtures.py">

```python