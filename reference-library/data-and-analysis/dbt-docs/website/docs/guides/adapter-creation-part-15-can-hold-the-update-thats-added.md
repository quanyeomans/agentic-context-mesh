# can hold the '_update' that's added
schema_seed_added_yml = """
version: 2
seeds:
  - name: added
    config:
      column_types:
        name: varchar(64)
"""

class TestSnapshotCheckColsRedshift(BaseSnapshotCheckCols):
    # Redshift defines the 'name' column such that it's not big enough
    # to hold the '_update' added in the test.
    @pytest.fixture(scope="class")
    def models(self):
        return {
            "base.csv": seeds_base_csv,
            "added.csv": seeds_added_csv,
            "seeds.yml": schema_seed_added_yml,
        }
```

</File>

As another example, the `dbt-bigquery` adapter asks users to "authorize" replacing a <Term id="table" /> with a <Term id="view" /> by supplying the `--full-refresh` flag. The reason: In the table <Term id="materialization" /> logic, a view by the same name must first be dropped; if the table query fails, the model will be missing.

Knowing this possibility, the "base" test case offers a `require_full_refresh` switch on the `test_config` fixture class. For BigQuery, we'll switch it on:

<File name="tests/functional/adapter/test_basic.py">

```python
import pytest
from dbt.tests.adapter.basic.test_base import BaseSimpleMaterializations

class TestSimpleMaterializationsBigQuery(BaseSimpleMaterializations):
    @pytest.fixture(scope="class")
    def test_config(self):
        # effect: add '--full-refresh' flag in requisite 'dbt run' step
        return {"require_full_refresh": True}
```

</File>

It's always worth asking whether the required modifications represent gaps in perceived or expected dbt functionality. Are these simple implementation details, which any user of this database would understand? Are they limitations worth documenting?

If, on the other hand, they represent poor assumptions in the "basic" test cases, which fail to account for a common pattern in other types of databases-—please open an issue or PR in the `dbt-core` repository on GitHub.

### Running with multiple profiles

Some databases support multiple connection methods, which map to actually different functionality behind the scenes. For instance, the `dbt-spark` adapter supports connections to Apache Spark clusters _and_ Databricks runtimes, which supports additional functionality out of the box, enabled by the Delta file format.

<File name="tests/conftest.py">

```python
def pytest_addoption(parser):
    parser.addoption("--profile", action="store", default="apache_spark", type=str)