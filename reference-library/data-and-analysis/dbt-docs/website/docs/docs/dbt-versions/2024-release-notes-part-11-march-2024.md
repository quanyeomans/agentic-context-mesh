## March 2024

- **New:** The <Constant name="semantic_layer" /> services now support using Privatelink for customers who have it enabled.
- **New:** You can now develop against and test your <Constant name="semantic_layer" /> in the dbt CLI if your developer credential uses SSO.
- **Enhancement:** You can select entities to Group By, Filter By, and Order By.
- **Fix:** `dbt parse` no longer shows an error when you use a list of filters (instead of just a string filter) on a metric.
- **Fix:** `join_to_timespine` now properly gets applied to conversion metric input measures.
- **Fix:** Fixed an issue where exports in Redshift were not always committing to the DWH, which also had the side-effect of leaving table locks open.
- **Behavior change:** Introduced the `source_freshness_run_project_hooks` flag, opt-in and disabled by default. If set to `True`, dbt will include `on-run-*` project hooks in the `source freshness` command. This will become the default in a future version of dbt. Read [Project hooks with source freshness](/reference/global-configs/behavior-changes#project-hooks-with-source-freshness) for more information.