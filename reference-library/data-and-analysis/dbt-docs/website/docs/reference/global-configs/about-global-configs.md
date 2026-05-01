---
title: "About flags (global configs)"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

In dbt, "flags" (also called "global configs") are configurations for fine-tuning _how_ dbt runs your project. They differ from [resource-specific configs](/reference/configs-and-properties) that tell dbt about _what_ to run.

Flags control things like the visual output of logs, whether to treat specific warning messages as errors, or whether to "fail fast" after encountering the first error. Flags are "global" configs because they are available for all dbt commands and they can be set in multiple places.

You can use flags with the <Constant name="fusion_engine"/> or <Constant name="core"/> engine through the CLI during local development or in <Constant name="dbt_platform"/>.

There is a significant overlap between dbt's flags and dbt's command line options, but there are differences:
- Certain flags can only be set in [`dbt_project.yml`](/reference/dbt_project.yml) and cannot be overridden for specific invocations via CLI options.
- If a CLI option is supported by specific commands, rather than supported by all commands ("global"), it is generally not considered to be a "flag".

### Setting flags

import SettingFlags from '/snippets/_setting-flags.md';

<SettingFlags />

### Accessing flags

Custom user-defined logic, written in Jinja, can check the values of flags using [the `flags` context variable](/reference/dbt-jinja-functions/flags).

```yaml
# dbt_project.yml

on-run-start:
  - '{{ log("I will stop at the first sign of trouble", info = true) if flags.FAIL_FAST }}'
```

## Available flags

Because the values of `flags` can differ across invocations, we strongly advise against using `flags` as an input to configurations or dependencies (`ref` + `source`) that dbt resolves [during parsing](/reference/parsing#known-limitations).

### Common flag examples

Use the `--target` flag to specify which target (environment) to use when running dbt commands. For example:

```bash
dbt run --target dev
dbt run --target prod
dbt build --target staging
```

The `--target` flag allows you to run the same dbt project against different environments without modifying your configuration files. Define the target in your `profiles.yml` file. Learn more about [connection profiles and targets](/docs/local/profiles.yml#understanding-targets-in-profiles).

The following table lists all the available flags in dbt, the type, default value, whether it can be set in the project file, the environment variable it corresponds to, and the CLI flags it corresponds to. 

<VersionBlock lastVersion="1.10">


| Flag | dbt platform CLI? | Type / default | In project? | Env var | CLI flags |
|------|----------------|----------------|-------------|---------|-----------|
| [cache_selected_only](/reference/global-configs/cache) | ✅ | boolean  default: False | ✅ | `DBT_CACHE_SELECTED_ONLY` | `--cache-selected-only`  `--no-cache-selected-only` |
| [clean_project_files_only](/reference/commands/clean#--clean-project-files-only) | ❌ | boolean  default: True | ❌ | `DBT_CLEAN_PROJECT_FILES_ONLY` | `--clean-project-files-only`  `--no-clean-project-files-only` |
| [debug](/reference/global-configs/logs#debug-level-logging) | ✅ | boolean  default: False | ✅ | `DBT_DEBUG` | `--debug`  `--no-debug` |
| [defer](/reference/node-selection/defer) | ✅ (default) | boolean  default: False | ❌ | `DBT_DEFER` | `--defer`  `--no-defer` |
| [defer_state](/reference/node-selection/defer) | ❌ | path  default: None | ❌ | `DBT_DEFER_STATE` | `--defer-state` |
| [favor_state](/reference/node-selection/defer#favor-state) | ✅ | boolean  default: False | ❌ | `DBT_FAVOR_STATE` | `--favor-state`  `--no-favor-state` |
| [empty](/docs/build/empty-flag) | ✅ | boolean  default: False | ❌ | `DBT_EMPTY` | `--empty`  `--no-empty` |
| [event_time_start](/reference/dbt-jinja-functions/model#batch-properties-for-microbatch-models) | ✅ | datetime  default: None | ❌ | `DBT_EVENT_TIME_START` | `--event-time-start` |
| [event_time_end](/reference/dbt-jinja-functions/model#batch-properties-for-microbatch-models) | ✅ | datetime  default: None | ❌ | `DBT_EVENT_TIME_END` | `--event-time-end` |
| [fail_fast](/reference/global-configs/failing-fast) | ✅ | boolean  default: False | ✅ | `DBT_FAIL_FAST` | `--fail-fast`  `-x`  `--no-fail-fast` |
| [full_refresh](/reference/resource-configs/full_refresh) | ✅ | boolean  default: False | ✅ (as resource config) | `DBT_FULL_REFRESH` | `--full-refresh`  `--no-full-refresh` |
| [indirect_selection](/reference/node-selection/test-selection-examples#syntax-examples) | ❌ | enum  default: eager | ✅ | `DBT_INDIRECT_SELECTION` | `--indirect-selection` |
| [introspect](/reference/commands/compile#introspective-queries) | ❌ | boolean  default: True | ❌ | `DBT_INTROSPECT` | `--introspect`  `--no-introspect` |
| [log_cache_events](/reference/global-configs/logs#logging-relational-cache-events) | ❌ | boolean  default: False | ❌ | `DBT_LOG_CACHE_EVENTS` | `--log-cache-events`  `--no-log-cache-events` |
| [log_format_file](/reference/global-configs/logs#log-formatting) | ❌ | enum  default: default (text) | ✅ | `DBT_LOG_FORMAT_FILE` | `--log-format-file` |
| [log_format](/reference/global-configs/logs#log-formatting) | ❌ | enum  default: default (text) | ✅ | `DBT_LOG_FORMAT` | `--log-format` |
| [log_level_file](/reference/global-configs/logs#log-level) | ❌ | enum  default: debug | ✅ | `DBT_LOG_LEVEL_FILE` | `--log-level-file` |
| [log_level](/reference/global-configs/logs#log-level) | ❌ | enum  default: info | ✅ | `DBT_LOG_LEVEL` | `--log-level` |
| [log_path](/reference/global-configs/logs) | ❌ | path  default: None (uses `logs/`) | ❌ | `DBT_LOG_PATH` | `--log-path` |
| [partial_parse](/reference/global-configs/parsing#partial-parsing) | ✅ | boolean  default: True | ✅ | `DBT_PARTIAL_PARSE` | `--partial-parse`  `--no-partial-parse` |
| [populate_cache](/reference/global-configs/cache) | ✅ | boolean  default: True | ✅ | `DBT_POPULATE_CACHE` | `--populate-cache`  `--no-populate-cache` |
| [print](/reference/global-configs/print-output#suppress-print-messages-in-stdout) | ❌ | boolean  default: True | ❌ | `DBT_PRINT` | `--print`  `--no-print` |
| [printer_width](/reference/global-configs/print-output#printer-width) | ❌ | int  default: 80 | ✅ | `DBT_PRINTER_WIDTH` | `--printer-width` |
| [profile](/docs/local/profiles.yml#about-profiles) | ❌ | string  default: None | ✅ (as top-level key) | `DBT_PROFILE`  | [`--profile`](/docs/local/profiles.yml#overriding-profiles-and-targets) |
| [profiles_dir](/docs/local/profiles.yml#about-profiles) | ❌ | path  default: None (current dir, then HOME dir) | ❌ | `DBT_PROFILES_DIR` | `--profiles-dir` |
| [project_dir](/reference/dbt_project.yml) | ❌ | path  default: (empty) | ❌ | `DBT_PROJECT_DIR` | `--project-dir` |
| [quiet](/reference/global-configs/logs#suppress-non-error-logs-in-output) | ✅ | boolean  default: False | ❌ | `DBT_QUIET` | `--quiet` |
| [resource-type](/reference/global-configs/resource-type) (v1.8+) | ✅ | string  default: None | ❌ | `DBT_RESOURCE_TYPES`  `DBT_EXCLUDE_RESOURCE_TYPES` | `--resource-type`  `--exclude-resource-type` |
| [sample](/docs/build/sample-flag) | ✅ | string  default: None | ❌ | `DBT_SAMPLE` | `--sample` |
| [send_anonymous_usage_stats](/reference/global-configs/usage-stats) | ❌ | boolean  default: True | ✅ | `DBT_SEND_ANONYMOUS_USAGE_STATS` | `--send-anonymous-usage-stats`  `--no-send-anonymous-usage-stats` |
| [source_freshness_run_project_hooks](/reference/global-configs/behavior-changes#source_freshness_run_project_hooks) | ❌ | boolean  default: True | ✅ | ❌ | ❌ |
| [state](/reference/node-selection/defer) | ❌ | path  default: none | ❌ | `DBT_STATE`, `DBT_DEFER_STATE` | `--state`  `--defer-state` |
| [static_parser](/reference/global-configs/parsing#static-parser) | ❌ | boolean  default: True | ✅ | `DBT_STATIC_PARSER` | `--static-parser`  `--no-static-parser` |
| [store_failures](/reference/resource-configs/store_failures) | ✅ | boolean  default: False | ✅ (as resource config) | `DBT_STORE_FAILURES` | `--store-failures`  `--no-store-failures` |
| [target_path](/reference/global-configs/json-artifacts) | ❌ | path  default: None (uses `target/`) | ❌ | `DBT_TARGET_PATH` | `--target-path` |
| [target](/docs/local/profiles.yml#about-profiles) | ❌ | string  default: None | ❌ | `DBT_TARGET` | [`--target`](/docs/local/profiles.yml#overriding-profiles-and-targets) |
| [use_colors_file](/reference/global-configs/logs#color) | ❌ | boolean  default: True | ✅ | `DBT_USE_COLORS_FILE` | `--use-colors-file`  `--no-use-colors-file` |
| [use_colors](/reference/global-configs/print-output#print-color) | ❌ | boolean  default: True | ✅ | `DBT_USE_COLORS` | `--use-colors`  `--no-use-colors` |
| [use_experimental_parser](/reference/global-configs/parsing#experimental-parser) | ❌ | boolean  default: False | ✅ | `DBT_USE_EXPERIMENTAL_PARSER` | `--use-experimental-parser`  `--no-use-experimental-parser` |
| [version_check](/reference/global-configs/version-compatibility) | ❌ | boolean  default: varies | ✅ | `DBT_VERSION_CHECK` | `--version-check`  `--no-version-check` |
| [warn_error_options](/reference/global-configs/warnings) | ✅ | dict  default: {} | ✅ | `DBT_WARN_ERROR_OPTIONS` | `--warn-error-options` |
| [warn_error](/reference/global-configs/warnings) | ✅ | boolean  default: False | ✅ | `DBT_WARN_ERROR` | `--warn-error` |
| [write_json](/reference/global-configs/json-artifacts) | ✅ | boolean  default: True | ✅ | `DBT_WRITE_JSON` | `--write-json`  `--no-write-json` |


</VersionBlock>

<VersionBlock firstVersion="1.11">


| Flag | dbt platform CLI? | Type / default | In project? | Env var | CLI flags |
|------|----------------|----------------|-------------|---------|-----------|
| [cache_selected_only](/reference/global-configs/cache) | ✅ | boolean  default: False | ✅ | `DBT_ENGINE_CACHE_SELECTED_ONLY` | `--cache-selected-only`  `--no-cache-selected-only` |
| [clean_project_files_only](/reference/commands/clean#--clean-project-files-only) | ❌ | boolean  default: True | ❌ | `DBT_ENGINE_CLEAN_PROJECT_FILES_ONLY` | `--clean-project-files-only`  `--no-clean-project-files-only` |
| [debug](/reference/global-configs/logs#debug-level-logging) | ✅ | boolean  default: False | ✅ | `DBT_ENGINE_DEBUG` | `--debug`  `--no-debug` |
| [defer](/reference/node-selection/defer) | ✅ (default) | boolean  default: False | ❌ | `DBT_ENGINE_DEFER` | `--defer`  `--no-defer` |
| [defer_state](/reference/node-selection/defer) | ❌ | path  default: None | ❌ | `DBT_ENGINE_DEFER_STATE` | `--defer-state` |
| [favor_state](/reference/node-selection/defer#favor-state) | ✅ | boolean  default: False | ❌ | `DBT_ENGINE_FAVOR_STATE` | `--favor-state`  `--no-favor-state` |
| [empty](/docs/build/empty-flag) | ✅ | boolean  default: False | ❌ | `DBT_ENGINE_EMPTY` | `--empty`  `--no-empty` |
| [event_time_start](/reference/dbt-jinja-functions/model#batch-properties-for-microbatch-models) | ✅ | datetime  default: None | ❌ | `DBT_ENGINE_EVENT_TIME_START` | `--event-time-start` |
| [event_time_end](/reference/dbt-jinja-functions/model#batch-properties-for-microbatch-models) | ✅ | datetime  default: None | ❌ | `DBT_ENGINE_EVENT_TIME_END` | `--event-time-end` |
| [fail_fast](/reference/global-configs/failing-fast) | ✅ | boolean  default: False | ✅ | `DBT_ENGINE_FAIL_FAST` | `--fail-fast`  `-x`  `--no-fail-fast` |
| [full_refresh](/reference/resource-configs/full_refresh) | ✅ | boolean  default: False | ✅ (as resource config) | `DBT_ENGINE_FULL_REFRESH` | `--full-refresh`  `--no-full-refresh` |
| [indirect_selection](/reference/node-selection/test-selection-examples#syntax-examples) | ❌ | enum  default: eager | ✅ | `DBT_ENGINE_INDIRECT_SELECTION` | `--indirect-selection` |
| [introspect](/reference/commands/compile#introspective-queries) | ❌ | boolean  default: True | ❌ | `DBT_ENGINE_INTROSPECT` | `--introspect`  `--no-introspect` |
| [log_cache_events](/reference/global-configs/logs#logging-relational-cache-events) | ❌ | boolean  default: False | ❌ | `DBT_ENGINE_LOG_CACHE_EVENTS` | `--log-cache-events`  `--no-log-cache-events` |
| [log_format_file](/reference/global-configs/logs#log-formatting) | ❌ | enum  default: default (text) | ✅ | `DBT_ENGINE_LOG_FORMAT_FILE` | `--log-format-file` |
| [log_format](/reference/global-configs/logs#log-formatting) | ❌ | enum  default: default (text) | ✅ | `DBT_ENGINE_LOG_FORMAT` | `--log-format` |
| [log_level_file](/reference/global-configs/logs#log-level) | ❌ | enum  default: debug | ✅ | `DBT_ENGINE_LOG_LEVEL_FILE` | `--log-level-file` |
| [log_level](/reference/global-configs/logs#log-level) | ❌ | enum  default: info | ✅ | `DBT_ENGINE_LOG_LEVEL` | `--log-level` |
| [log_path](/reference/global-configs/logs) | ❌ | path  default: None (uses `logs/`) | ❌ | `DBT_ENGINE_LOG_PATH` | `--log-path` |
| [partial_parse](/reference/global-configs/parsing#partial-parsing) | ✅ | boolean  default: True | ✅ | `DBT_ENGINE_PARTIAL_PARSE` | `--partial-parse`  `--no-partial-parse` |
| [populate_cache](/reference/global-configs/cache) | ✅ | boolean  default: True | ✅ | `DBT_ENGINE_POPULATE_CACHE` | `--populate-cache`  `--no-populate-cache` |
| [print](/reference/global-configs/print-output#suppress-print-messages-in-stdout) | ❌ | boolean  default: True | ❌ | `DBT_ENGINE_PRINT` | `--print`  `--no-print` |
| [printer_width](/reference/global-configs/print-output#printer-width) | ❌ | int  default: 80 | ✅ | `DBT_ENGINE_PRINTER_WIDTH` | `--printer-width` |
| [profile](/docs/core/connect-data-platform/connection-profiles#about-profiles) | ❌ | string  default: None | ✅ (as top-level key) | `DBT_ENGINE_PROFILE`  | [`--profile`](/docs/core/connect-data-platform/connection-profiles#overriding-profiles-and-targets) |
| [profiles_dir](/docs/core/connect-data-platform/connection-profiles#about-profiles) | ❌ | path  default: None (current dir, then HOME dir) | ❌ | `DBT_ENGINE_PROFILES_DIR` | `--profiles-dir` |
| [project_dir](/reference/dbt_project.yml) | ❌ | path  default: (empty) | ❌ | `DBT_ENGINE_PROJECT_DIR` | `--project-dir` |
| [quiet](/reference/global-configs/logs#suppress-non-error-logs-in-output) | ✅ | boolean  default: False | ❌ | `DBT_ENGINE_QUIET` | `--quiet` |
| [resource-type](/reference/global-configs/resource-type) (v1.8+) | ✅ | string  default: None | ❌ | `DBT_ENGINE_RESOURCE_TYPES`  `DBT_ENGINE_EXCLUDE_RESOURCE_TYPES` | `--resource-type`  `--exclude-resource-type` |
| [sample](/docs/build/sample-flag) | ✅ | string  default: None | ❌ | `DBT_ENGINE_SAMPLE` | `--sample` |
| [send_anonymous_usage_stats](/reference/global-configs/usage-stats) | ❌ | boolean  default: True | ✅ | `DBT_ENGINE_SEND_ANONYMOUS_USAGE_STATS` | `--send-anonymous-usage-stats`  `--no-send-anonymous-usage-stats` |
| [source_freshness_run_project_hooks](/reference/global-configs/behavior-changes#source_freshness_run_project_hooks) | ❌ | boolean  default: True | ✅ | ❌ | ❌ |
| [sqlparse](/reference/global-configs/sqlparse) | ❌ | YAML map  default: MAX_GROUPING_DEPTH and MAX_GROUPING_TOKENS set to null | ❌ | `DBT_ENGINE_SQLPARSE` | `--sqlparse` |
| [state](/reference/node-selection/defer) | ❌ | path  default: none | ❌ | `DBT_ENGINE_STATE`, `DBT_ENGINE_DEFER_STATE` | `--state`  `--defer-state` |
| [static_parser](/reference/global-configs/parsing#static-parser) | ❌ | boolean  default: True | ✅ | `DBT_ENGINE_STATIC_PARSER` | `--static-parser`  `--no-static-parser` |
| [store_failures](/reference/resource-configs/store_failures) | ✅ | boolean  default: False | ✅ (as resource config) | `DBT_ENGINE_STORE_FAILURES` | `--store-failures`  `--no-store-failures` |
| [target_path](/reference/global-configs/json-artifacts) | ❌ | path  default: None (uses `target/`) | ❌ | `DBT_ENGINE_TARGET_PATH` | `--target-path` |
| [target](/docs/core/connect-data-platform/connection-profiles#about-profiles) | ❌ | string  default: None | ❌ | `DBT_ENGINE_TARGET` | [`--target`](/docs/core/connect-data-platform/connection-profiles#overriding-profiles-and-targets) |
| [use_colors_file](/reference/global-configs/logs#color) | ❌ | boolean  default: True | ✅ | `DBT_ENGINE_USE_COLORS_FILE` | `--use-colors-file`  `--no-use-colors-file` |
| [use_colors](/reference/global-configs/print-output#print-color) | ❌ | boolean  default: True | ✅ | `DBT_ENGINE_USE_COLORS` | `--use-colors`  `--no-use-colors` |
| [use_experimental_parser](/reference/global-configs/parsing#experimental-parser) | ❌ | boolean  default: False | ✅ | `DBT_ENGINE_USE_EXPERIMENTAL_PARSER` | `--use-experimental-parser`  `--no-use-experimental-parser` |
| [version_check](/reference/global-configs/version-compatibility) | ❌ | boolean  default: varies | ✅ | `DBT_ENGINE_VERSION_CHECK` | `--version-check`  `--no-version-check` |
| [warn_error_options](/reference/global-configs/warnings) | ✅ | dict  default: {} | ✅ | `DBT_ENGINE_WARN_ERROR_OPTIONS` | `--warn-error-options` |
| [warn_error](/reference/global-configs/warnings) | ✅ | boolean  default: False | ✅ | `DBT_ENGINE_WARN_ERROR` | `--warn-error` |
| [write_json](/reference/global-configs/json-artifacts) | ✅ | boolean  default: True | ✅ | `DBT_ENGINE_WRITE_JSON` | `--write-json`  `--no-write-json` |

</VersionBlock>
