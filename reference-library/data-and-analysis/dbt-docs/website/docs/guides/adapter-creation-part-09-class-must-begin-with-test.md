# class must begin with 'Test'
class TestExample:
    """
    Methods in this class will be of two types:
    1. Fixtures defining the dbt "project" for this test case.
       These are scoped to the class, and reused for all tests in the class.
    2. Actual tests, whose names begin with 'test_'.
       These define sequences of dbt commands and 'assert' statements.
    """
    
    # configuration in dbt_project.yml
    @pytest.fixture(scope="class")
    def project_config_update(self):
        return {
          "name": "example",
          "models": {"+materialized": "view"}
        }

    # everything that goes in the "seeds" directory
    @pytest.fixture(scope="class")
    def seeds(self):
        return {
            "my_seed.csv": my_seed_csv,
        }

    # everything that goes in the "models" directory
    @pytest.fixture(scope="class")
    def models(self):
        return {
            "my_model.sql": my_model_sql,
            "my_model.yml": my_model_yml,
        }
        
    # continues below
```

</File>

3. Now that we've set up our project, it's time to define a sequence of dbt commands and assertions. We define one or more methods in the same file, on the same class (`TestExampleFailingTest`), whose names begin with `test_`. These methods share the same setup (project scenario) from above, but they can be run independently by pytest—so they shouldn't depend on each other in any way.

<File name="tests/functional/example/test_example_failing_test.py">

```python
    # continued from above

    # The actual sequence of dbt commands and assertions
    # pytest will take care of all "setup" + "teardown"
    def test_run_seed_test(self, project):
        """
        Seed, then run, then test. We expect one of the tests to fail
        An alternative pattern is to use pytest "xfail" (see below)
        """
        # seed seeds
        results = run_dbt(["seed"])
        assert len(results) == 1
        # run models
        results = run_dbt(["run"])
        assert len(results) == 1
        # test tests
        results = run_dbt(["test"], expect_pass = False) # expect failing test
        assert len(results) == 2
        # validate that the results include one pass and one failure
        result_statuses = sorted(r.status for r in results)
        assert result_statuses == ["fail", "pass"]

    @pytest.mark.xfail
    def test_build(self, project):
        """Expect a failing test"""
        # do it all
        results = run_dbt(["build"])
```

</File>

3. Our test is ready to run! The last step is to invoke `pytest` from your command line. We'll walk through the actual setup and configuration of `pytest` in the next section.

<File name="terminal">

```sh
$ python3 -m pytest tests/functional/test_example.py
=========================== test session starts ============================
platform ... -- Python ..., pytest-..., pluggy-...
rootdir: ...
plugins: ...

tests/functional/test_example.py .X                                  [100%]

======================= 1 passed, 1 xpassed in 1.38s =======================
```

</File>

You can find more ways to run tests, along with a full command reference, in the [pytest usage docs](https://docs.pytest.org/how-to/usage.html).

We've found the `-s` flag (or `--capture=no`) helpful to print logs from the underlying dbt invocations, and to step into an interactive debugger if you've added one. You can also use environment variables to set [global dbt configs](/reference/global-configs/about-global-configs), such as <VersionBlock lastVersion="1.10">`DBT_DEBUG`</VersionBlock><VersionBlock firstVersion="1.11">`DBT_ENGINE_DEBUG`</VersionBlock> (to show debug-level logs).

### Testing this adapter

Anyone who installs `dbt-core`, and wishes to define their own test cases, can use the framework presented in the first section. The framework is especially useful for testing standard dbt behavior across different databases.

To that end, we have built and made available a [package of reusable adapter test cases](https://github.com/dbt-labs/dbt-adapters/tree/main/dbt-tests-adapter), for creators and maintainers of adapter plugins. These test cases cover basic expected functionality, as well as functionality that frequently requires different implementations across databases.

For the time being, this package is also located within the `dbt-core` repository, but separate from the `dbt-core` Python package.

### Categories of tests

In the course of creating and maintaining your adapter, it's likely that you will end up implementing tests that fall into three broad categories:

1. **Basic tests** that every adapter plugin is expected to pass. These are defined in `tests.adapter.basic`. Given differences across data platforms, these may require slight modification or reimplementation. Significantly overriding or disabling these tests should be with good reason, since each represents basic functionality expected by dbt users. For example, if your adapter does not support incremental models, you should disable the test, [by marking it with `skip` or `xfail`](https://docs.pytest.org/en/latest/how-to/skipping.html), as well as noting that limitation in any documentation, READMEs, and usage guides that accompany your adapter.

2. **Optional tests**, for second-order functionality that is common across plugins, but not required for basic use. Your plugin can opt into these test cases by inheriting existing ones, or reimplementing them with adjustments. For now, this category includes all tests located outside the `basic` subdirectory. More tests will be added as we convert older tests defined on dbt-core and mature plugins to use the standard framework.

3. **Custom tests**, for behavior that is specific to your adapter / data platform. Each <Term id="data-warehouse" /> has its own specialties and idiosyncracies. We encourage you to use the same `pytest`-based framework, utilities, and fixtures to write your own custom tests for functionality that is unique to your adapter.

If you run into an issue with the core framework, or the basic/optional test cases—or if you've written a custom test that you believe would be relevant and useful for other adapter plugin developers—please open an issue or PR in the `dbt-core` repository on GitHub.

### Getting started running basic tests

In this section, we'll walk through the three steps to start running our basic test cases on your adapter plugin:

1. Install dependencies
2. Set up and configure pytest
3. Define test cases

### Install dependencies

You should already have a virtual environment with `dbt-core` and your adapter plugin installed. You'll also need to install:

- [`pytest`](https://pypi.org/project/pytest/)
- [`dbt-tests-adapter`](https://pypi.org/project/dbt-tests-adapter/), the set of common test cases
- (optional) [`pytest` plugins](https://docs.pytest.org/en/7.0.x/reference/plugin_list.html)--we'll use `pytest-dotenv` below

Or specify all dependencies in a requirements file like:
<File name="dev_requirements.txt">

```txt
pytest
pytest-dotenv
dbt-tests-adapter
```

</File>

```sh
python -m pip install -r dev_requirements.txt
```

### Set up and configure pytest

First, set yourself up to run `pytest` by creating a file named `pytest.ini` at the root of your repository:

<File name="pytest.ini">

```python
[pytest]
filterwarnings =
    ignore:.*'soft_unicode' has been renamed to 'soft_str'*:DeprecationWarning
    ignore:unclosed file .*:ResourceWarning
env_files =
    test.env  # uses pytest-dotenv plugin
              # this allows you to store env vars for database connection in a file named test.env
              # rather than passing them in every CLI command, or setting in `PYTEST_ADDOPTS`
              # be sure to add "test.env" to .gitignore as well!
testpaths =
    tests/functional  # name per convention
```

</File>

Then, create a configuration file within your tests directory. In it, you'll want to define all necessary profile configuration for connecting to your data platform in local development and continuous integration. We recommend setting these values with environment variables, since this file will be checked into version control.

<File name="tests/conftest.py">

```python
import pytest
import os