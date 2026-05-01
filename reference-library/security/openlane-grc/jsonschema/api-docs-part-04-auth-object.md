## auth: object

Auth settings including oauth2 providers and token configuration


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled authentication on the server, not recommended to disable|no|
|[**token**](#authtoken)|`object`||yes|
|[**supportedproviders**](#authsupportedproviders)|`string[]`||no|
|[**providers**](#authproviders)|`object`|OauthProviderConfig represents the configuration for OAuth providers such as Github and Google|no|

**Additional Properties:** not allowed  
**Example**

```json
{
    "token": {
        "keys": {},
        "redis": {
            "config": {}
        },
        "apitokens": {
            "keys": {}
        }
    },
    "providers": {
        "github": {},
        "google": {},
        "webauthn": {}
    }
}
```

<a name="authtoken"></a>
### auth\.token: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**kid**|`string`||yes|
|**audience**|`string`||yes|
|**refreshaudience**|`string`||no|
|**issuer**|`string`||yes|
|**accessduration**|`integer`||no|
|**refreshduration**|`integer`||no|
|**refreshoverlap**|`integer`||no|
|**jwksendpoint**|`string`||no|
|[**keys**](#authtokenkeys)|`object`||yes|
|**generatekeys**|`boolean`||no|
|**jwkscachettl**|`integer`||no|
|[**redis**](#authtokenredis)|`object`||no|
|[**apitokens**](#authtokenapitokens)|`object`||no|
|**assessmentaccessduration**|`integer`||no|
|**trustcenterndarequestaccessduration**|`integer`||no|

**Additional Properties:** not allowed  
**Example**

```json
{
    "keys": {},
    "redis": {
        "config": {}
    },
    "apitokens": {
        "keys": {}
    }
}
```

<a name="authtokenkeys"></a>
#### auth\.token\.keys: object

**Additional Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**Additional Properties**|`string`|||

<a name="authtokenredis"></a>
#### auth\.token\.redis: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|||
|[**config**](#authtokenredisconfig)|`object`|||
|**blacklistprefix**|`string`|||

**Additional Properties:** not allowed  
**Example**

```json
{
    "config": {}
}
```

<a name="authtokenredisconfig"></a>
##### auth\.token\.redis\.config: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|||
|**address**|`string`|||
|**name**|`string`|||
|**username**|`string`|||
|**password**|`string`|||
|**db**|`integer`|||
|**dialtimeout**|`integer`|||
|**readtimeout**|`integer`|||
|**writetimeout**|`integer`|||
|**maxretries**|`integer`|||
|**minidleconns**|`integer`|||
|**maxidleconns**|`integer`|||
|**maxactiveconns**|`integer`|||

**Additional Properties:** not allowed  
<a name="authtokenapitokens"></a>
#### auth\.token\.apitokens: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|||
|**envprefix**|`string`|||
|[**keys**](#authtokenapitokenskeys)|`object`|||
|**secretsize**|`integer`|||
|**delimiter**|`string`|||
|**prefix**|`string`|||

**Additional Properties:** not allowed  
**Example**

```json
{
    "keys": {}
}
```

<a name="authtokenapitokenskeys"></a>
##### auth\.token\.apitokens\.keys: object

**Additional Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|[**Additional Properties**](#authtokenapitokenskeysadditionalproperties)|`object`|||

<a name="authtokenapitokenskeysadditionalproperties"></a>
###### auth\.token\.apitokens\.keys\.additionalProperties: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**secret**|`string`|||
|**status**|`string`|||

**Additional Properties:** not allowed  
<a name="authsupportedproviders"></a>
### auth\.supportedproviders: array

**Items**

**Item Type:** `string`  
<a name="authproviders"></a>
### auth\.providers: object

OauthProviderConfig represents the configuration for OAuth providers such as Github and Google


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**redirecturl**|`string`|RedirectURL is the URL that the OAuth2 client will redirect to after authentication is complete||
|[**github**](#authprovidersgithub)|`object`||yes|
|[**google**](#authprovidersgoogle)|`object`||yes|
|[**webauthn**](#authproviderswebauthn)|`object`||yes|

**Additional Properties:** not allowed  
**Example**

```json
{
    "github": {},
    "google": {},
    "webauthn": {}
}
```

<a name="authprovidersgithub"></a>
#### auth\.providers\.github: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**clientid**|`string`||yes|
|**clientsecret**|`string`||yes|
|**clientendpoint**|`string`||no|
|[**scopes**](#authprovidersgithubscopes)|`string[]`||yes|
|**redirecturl**|`string`||yes|

**Additional Properties:** not allowed  
<a name="authprovidersgithubscopes"></a>
##### auth\.providers\.github\.scopes: array

**Items**

**Item Type:** `string`  
<a name="authprovidersgoogle"></a>
#### auth\.providers\.google: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**clientid**|`string`||yes|
|**clientsecret**|`string`||yes|
|**clientendpoint**|`string`||no|
|[**scopes**](#authprovidersgooglescopes)|`string[]`||yes|
|**redirecturl**|`string`||yes|

**Additional Properties:** not allowed  
<a name="authprovidersgooglescopes"></a>
##### auth\.providers\.google\.scopes: array

**Items**

**Item Type:** `string`  
<a name="authproviderswebauthn"></a>
#### auth\.providers\.webauthn: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`||no|
|**displayname**|`string`||yes|
|**relyingpartyid**|`string`||yes|
|[**requestorigins**](#authproviderswebauthnrequestorigins)|`string[]`||yes|
|**maxdevices**|`integer`||no|
|**enforcetimeout**|`boolean`||no|
|**timeout**|`integer`||no|
|**debug**|`boolean`||no|

**Additional Properties:** not allowed  
<a name="authproviderswebauthnrequestorigins"></a>
##### auth\.providers\.webauthn\.requestorigins: array

**Items**

**Item Type:** `string`  
<a name="authz"></a>