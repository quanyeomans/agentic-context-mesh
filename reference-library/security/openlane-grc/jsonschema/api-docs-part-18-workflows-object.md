## workflows: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|||
|[**cel**](#workflowscel)|`object`|||
|[**gala**](#workflowsgala)|`object`|||

**Additional Properties:** not allowed  
**Example**

```json
{
    "cel": {},
    "gala": {}
}
```

<a name="workflowscel"></a>
### workflows\.cel: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**timeout**|`integer`|||
|**costlimit**|`integer`|||
|**interruptcheckfrequency**|`integer`|||
|**parserrecursionlimit**|`integer`|||
|**parserexpressionsizelimit**|`integer`|||
|**comprehensionnestinglimit**|`integer`|||
|**extendedvalidations**|`boolean`|||
|**optionaltypes**|`boolean`|||
|**identifierescapesyntax**|`boolean`|||
|**crosstypenumericcomparisons**|`boolean`|||
|**macrocalltracking**|`boolean`|||
|**evaloptimize**|`boolean`|||
|**trackstate**|`boolean`|||

**Additional Properties:** not allowed  
<a name="workflowsgala"></a>
### workflows\.gala: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|||
|**workercount**|`integer`|||
|**maxretries**|`integer`|||
|**failonenqueueerror**|`boolean`|||
|**queuename**|`string`|||

**Additional Properties:** not allowed  
<a name="campaignwebhook"></a>