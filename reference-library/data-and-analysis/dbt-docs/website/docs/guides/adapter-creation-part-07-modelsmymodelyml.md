# models/my_model.yml
my_model_yml = """
version: 2
models:
  - name: my_model
    columns:
      - name: id
        data_tests:
          - unique
          - not_null  # this test will fail
"""
```

</File>

2. Use the "fixtures" to define the project for your test case. These fixtures are always scoped to the **class**, where the class represents one test case—that is, one dbt project or scenario. (The same test case can be used for one or more actual tests, which we'll see in step 3.) Following the default pytest configurations, the file name must begin with `test_`, and the class name must begin with `Test`.

<File name="tests/functional/example/test_example_failing_test.py">

```python
import pytest
from dbt.tests.util import run_dbt