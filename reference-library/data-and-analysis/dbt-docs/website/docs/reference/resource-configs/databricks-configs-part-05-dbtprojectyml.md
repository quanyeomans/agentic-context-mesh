# dbt_project.yml
models:
  project_name:
    subfolder:
      # set defaults for all .py models defined in this subfolder
      +submission_method: all_purpose_cluster
      +create_notebook: False
      +cluster_id: abcd-1234-wxyz
```

If not configured, `dbt-spark` will use the built-in defaults: the all-purpose cluster (based on `cluster` in your connection profile) without creating a notebook. The `dbt-databricks` adapter will default to the cluster configured in `http_path`. We encourage explicitly configuring the clusters for Python models in Databricks projects.

**Installing packages:** When using all-purpose clusters, we recommend installing packages which you will be using to run your Python models.

**Related docs:**
- [PySpark DataFrame syntax](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.html)
- [Databricks: Introduction to DataFrames - Python](https://docs.databricks.com/spark/latest/dataframes-datasets/introduction-to-dataframes-python.html)