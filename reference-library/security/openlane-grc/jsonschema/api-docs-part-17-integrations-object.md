## integrations: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|[**githubapp**](#integrationsgithubapp)|`object`|||
|[**slack**](#integrationsslack)|`object`|||
|[**googleworkspace**](#integrationsgoogleworkspace)|`object`|||
|[**azureentraid**](#integrationsazureentraid)|`object`|||
|[**microsoftteams**](#integrationsmicrosoftteams)|`object`|||
|[**oidclocal**](#integrationsoidclocal)|`object`|||

**Additional Properties:** not allowed  
**Example**

```json
{
    "githubapp": {},
    "slack": {},
    "googleworkspace": {},
    "azureentraid": {},
    "microsoftteams": {},
    "oidclocal": {}
}
```

<a name="integrationsgithubapp"></a>
### integrations\.githubapp: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**appid**|`string`|||
|**privatekey**|`string`|||
|**webhooksecret**|`string`|||
|**appslug**|`string`|||

**Additional Properties:** not allowed  
<a name="integrationsslack"></a>
### integrations\.slack: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**clientid**|`string`|||
|**clientsecret**|`string`|||
|**redirecturl**|`string`|||

**Additional Properties:** not allowed  
<a name="integrationsgoogleworkspace"></a>
### integrations\.googleworkspace: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**clientid**|`string`|||
|**clientsecret**|`string`|||
|**redirecturl**|`string`|||

**Additional Properties:** not allowed  
<a name="integrationsazureentraid"></a>
### integrations\.azureentraid: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**clientid**|`string`|||
|**clientsecret**|`string`|||
|**redirecturl**|`string`|||

**Additional Properties:** not allowed  
<a name="integrationsmicrosoftteams"></a>
### integrations\.microsoftteams: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**clientid**|`string`|||
|**clientsecret**|`string`|||
|**redirecturl**|`string`|||

**Additional Properties:** not allowed  
<a name="integrationsoidclocal"></a>
### integrations\.oidclocal: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|||
|**clientid**|`string`|||
|**clientsecret**|`string`|||
|**discoveryurl**|`string`|||
|**redirecturl**|`string`|||

**Additional Properties:** not allowed  
<a name="workflows"></a>