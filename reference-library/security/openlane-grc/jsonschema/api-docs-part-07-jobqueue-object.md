## jobqueue: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**connectionuri**|`string`|||
|**runmigrations**|`boolean`|||
|[**riverconf**](#jobqueueriverconf)|`object`|||
|[**metrics**](#jobqueuemetrics)|`object`|||

**Additional Properties:** not allowed  
**Example**

```json
{
    "riverconf": {
        "Logger": {},
        "PeriodicJobs": [
            {}
        ],
        "Queues": {},
        "Test": {},
        "Workers": {}
    },
    "metrics": {}
}
```

<a name="jobqueueriverconf"></a>
### jobqueue\.riverconf: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**AdvisoryLockPrefix**|`integer`|||
|**CancelledJobRetentionPeriod**|`integer`|||
|**CompletedJobRetentionPeriod**|`integer`|||
|**DiscardedJobRetentionPeriod**|`integer`|||
|**ErrorHandler**||||
|**FetchCooldown**|`integer`|||
|**FetchPollInterval**|`integer`|||
|**ID**|`string`|||
|**JobCleanerTimeout**|`integer`|||
|[**JobInsertMiddleware**](#jobqueueriverconfjobinsertmiddleware)|`array`|||
|**JobTimeout**|`integer`|||
|[**Hooks**](#jobqueueriverconfhooks)|`array`|||
|[**Logger**](#jobqueueriverconflogger)|`object`|||
|**MaxAttempts**|`integer`|||
|[**Middleware**](#jobqueueriverconfmiddleware)|`array`|||
|[**PeriodicJobs**](#jobqueueriverconfperiodicjobs)|`array`|||
|**PollOnly**|`boolean`|||
|[**Queues**](#jobqueueriverconfqueues)|`object`|||
|**ReindexerSchedule**||||
|**ReindexerTimeout**|`integer`|||
|**RescueStuckJobsAfter**|`integer`|||
|**RetryPolicy**||||
|**Schema**|`string`|||
|**SkipJobKindValidation**|`boolean`|||
|**SkipUnknownJobCheck**|`boolean`|||
|[**Test**](#jobqueueriverconftest)|`object`|||
|**TestOnly**|`boolean`|||
|[**Workers**](#jobqueueriverconfworkers)|`object`|||
|[**WorkerMiddleware**](#jobqueueriverconfworkermiddleware)|`array`|||

**Additional Properties:** not allowed  
**Example**

```json
{
    "Logger": {},
    "PeriodicJobs": [
        {}
    ],
    "Queues": {},
    "Test": {},
    "Workers": {}
}
```

<a name="jobqueueriverconfjobinsertmiddleware"></a>
#### jobqueue\.riverconf\.JobInsertMiddleware: array

**Items**

<a name="jobqueueriverconfhooks"></a>
#### jobqueue\.riverconf\.Hooks: array

**Items**

<a name="jobqueueriverconflogger"></a>
#### jobqueue\.riverconf\.Logger: object

**No properties.**

**Additional Properties:** not allowed  
<a name="jobqueueriverconfmiddleware"></a>
#### jobqueue\.riverconf\.Middleware: array

**Items**

<a name="jobqueueriverconfperiodicjobs"></a>
#### jobqueue\.riverconf\.PeriodicJobs: array

**Items**

**Example**

```json
[
    {}
]
```

<a name="jobqueueriverconfqueues"></a>
#### jobqueue\.riverconf\.Queues: object

**Additional Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|[**Additional Properties**](#jobqueueriverconfqueuesadditionalproperties)|`object`|||

<a name="jobqueueriverconfqueuesadditionalproperties"></a>
##### jobqueue\.riverconf\.Queues\.additionalProperties: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**FetchCooldown**|`integer`|||
|**FetchPollInterval**|`integer`|||
|**MaxWorkers**|`integer`|||

**Additional Properties:** not allowed  
<a name="jobqueueriverconftest"></a>
#### jobqueue\.riverconf\.Test: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**DisableUniqueEnforcement**|`boolean`|||
|**Time**||||

**Additional Properties:** not allowed  
<a name="jobqueueriverconfworkers"></a>
#### jobqueue\.riverconf\.Workers: object

**No properties.**

**Additional Properties:** not allowed  
<a name="jobqueueriverconfworkermiddleware"></a>
#### jobqueue\.riverconf\.WorkerMiddleware: array

**Items**

<a name="jobqueuemetrics"></a>
### jobqueue\.metrics: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enablemetrics**|`boolean`|||
|**metricsdurationunit**|`string`|||
|**enablesemanticmetrics**|`boolean`|||

**Additional Properties:** not allowed  
<a name="redis"></a>