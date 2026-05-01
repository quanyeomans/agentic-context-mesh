## authz: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|enables authorization checks with openFGA|no|
|**storename**|`string`|name of openFGA store|no|
|**hosturl**|`string`|host url with scheme of the openFGA API|yes|
|**storeid**|`string`|id of openFGA store|no|
|**modelid**|`string`|id of openFGA model|no|
|**createnewmodel**|`boolean`|force create a new model|no|
|**modelfile**|`string`|path to the fga model file|no|
|**modulefile**|`string`|path to the fga module file|no|
|[**credentials**](#authzcredentials)|`object`||no|
|**maxbatchwritesize**|`integer`|maximum number of writes per batch in a transaction|no|

**Additional Properties:** not allowed  
**Example**

```json
{
    "credentials": {}
}
```

<a name="authzcredentials"></a>
### authz\.credentials: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**apitoken**|`string`|api token for the openFGA client||
|**clientid**|`string`|client id for the openFGA client||
|**clientsecret**|`string`|client secret for the openFGA client||
|**audience**|`string`|audience for the openFGA client||
|**issuer**|`string`|issuer for the openFGA client||
|**scopes**|`string`|scopes for the openFGA client||

**Additional Properties:** not allowed  
<a name="db"></a>