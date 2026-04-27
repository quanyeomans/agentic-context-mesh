---
title: "Workflow Definition"
source: Openlane GRC Platform
source_url: https://github.com/theopenlane/core
licence: Apache-2.0
domain: security
subdomain: openlane-grc
date_added: 2026-04-25
---

# Workflow Definition

Schema for Openlane workflow definitions


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**name**|`string`|||
|**description**|`string`|||
|**schemaType**|`string`|||
|**workflowKind**|`string`|Enum: `"APPROVAL"`, `"LIFECYCLE"`, `"NOTIFICATION"`||
|**approvalSubmissionMode**|`string`|Enum: `"MANUAL_SUBMIT"`, `"AUTO_SUBMIT"`||
|**approvalTiming**|`string`|Enum: `"PRE_COMMIT"`, `"POST_COMMIT"`||
|**version**|`string`|||
|[**targets**](#targets)|`object`|||
|[**triggers**](#triggers)|`array`|||
|[**conditions**](#conditions)|`array`|||
|[**actions**](#actions)|`array`|||
|[**metadata**](#metadata)|`object`|||

**Additional Properties:** not allowed  
**Example**

```json
{
    "targets": {},
    "triggers": [
        {
            "selector": {}
        }
    ],
    "conditions": [
        {}
    ],
    "actions": [
        {}
    ],
    "metadata": {}
}
```

<a name="targets"></a>
## targets: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|[**tagIds**](#targetstagids)|`string[]`|||
|[**groupIds**](#targetsgroupids)|`string[]`|||
|[**objectTypes**](#targetsobjecttypes)|`string[]`|||

**Additional Properties:** not allowed  
<a name="targetstagids"></a>
### targets\.tagIds\[\]: array

**Items**

**Item Type:** `string`  
<a name="targetsgroupids"></a>
### targets\.groupIds\[\]: array

**Items**

**Item Type:** `string`  
<a name="targetsobjecttypes"></a>
### targets\.objectTypes\[\]: array

**Items**


The object type the workflow applies to

**Item Type:** `string`  
**Item Enum:** `"ActionPlan"`, `"Campaign"`, `"CampaignTarget"`, `"Control"`, `"Evidence"`, `"IdentityHolder"`, `"InternalPolicy"`, `"Platform"`, `"Procedure"`, `"Subcontrol"`  
<a name="triggers"></a>
## triggers\[\]: array

**Items**

**Example**

```json
[
    {
        "selector": {}
    }
]
```

<a name="conditions"></a>
## conditions\[\]: array

**Items**

**Example**

```json
[
    {}
]
```

<a name="actions"></a>
## actions\[\]: array

**Items**

**Example**

```json
[
    {}
]
```

<a name="metadata"></a>
## metadata: object

**No properties.**
