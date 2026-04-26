## objectstorage: object

ProviderConfig contains configuration for object storage providers


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled indicates if object storage is enabled||
|[**keys**](#objectstoragekeys)|`string[]`|||
|**maxsizemb**|`integer`|MaxSizeMB is the maximum file size allowed in MB||
|**maxmemorymb**|`integer`|MaxMemoryMB is the maximum memory to use for file uploads in MB||
|**devmode**|`boolean`|DevMode automatically configures a local disk storage provider (and ensures directories exist) and ignores other provider configs||
|[**providers**](#objectstorageproviders)|`object`|||

**Additional Properties:** not allowed  
**Example**

```json
{
    "providers": {
        "s3": {
            "credentials": {}
        },
        "r2": {
            "credentials": {}
        },
        "disk": {
            "credentials": {}
        },
        "database": {
            "credentials": {}
        }
    }
}
```

<a name="objectstoragekeys"></a>
### objectstorage\.keys: array

**Items**

**Item Type:** `string`  
<a name="objectstorageproviders"></a>
### objectstorage\.providers: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|[**s3**](#objectstorageproviderss3)|`object`|ProviderConfigs contains configuration for all storage providers This is structured to allow easy extension for additional providers in the future||
|[**r2**](#objectstorageprovidersr2)|`object`|ProviderConfigs contains configuration for all storage providers This is structured to allow easy extension for additional providers in the future||
|[**disk**](#objectstorageprovidersdisk)|`object`|ProviderConfigs contains configuration for all storage providers This is structured to allow easy extension for additional providers in the future||
|[**database**](#objectstorageprovidersdatabase)|`object`|ProviderConfigs contains configuration for all storage providers This is structured to allow easy extension for additional providers in the future||

**Additional Properties:** not allowed  
**Example**

```json
{
    "s3": {
        "credentials": {}
    },
    "r2": {
        "credentials": {}
    },
    "disk": {
        "credentials": {}
    },
    "database": {
        "credentials": {}
    }
}
```

<a name="objectstorageproviderss3"></a>
#### objectstorage\.providers\.s3: object

ProviderConfigs contains configuration for all storage providers This is structured to allow easy extension for additional providers in the future


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled indicates if this provider is enabled||
|**ensureavailable**|`boolean`|EnsureAvailable enforces provider availability before completing server startup||
|**region**|`string`|Region for cloud providers||
|**bucket**|`string`|Bucket name for cloud providers||
|**endpoint**|`string`|Endpoint for custom endpoints||
|**proxypresignenabled**|`boolean`|ProxyPresignEnabled toggles proxy-signed download URL generation||
|**baseurl**|`string`|BaseURL is the prefix for proxy download URLs (e.g., http://localhost:17608/v1/files).||
|[**credentials**](#objectstorageproviderss3credentials)|`object`|ProviderCredentials contains credentials for a storage provider||

**Additional Properties:** not allowed  
**Example**

```json
{
    "credentials": {}
}
```

<a name="objectstorageproviderss3credentials"></a>
##### objectstorage\.providers\.s3\.credentials: object

ProviderCredentials contains credentials for a storage provider


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**accesskeyid**|`string`|AccessKeyID for cloud providers||
|**secretaccesskey**|`string`|SecretAccessKey for cloud providers||
|**projectid**|`string`|ProjectID for GCS||
|**accountid**|`string`|AccountID for Cloudflare R2||
|**apitoken**|`string`|APIToken for Cloudflare R2||

**Additional Properties:** not allowed  
<a name="objectstorageprovidersr2"></a>
#### objectstorage\.providers\.r2: object

ProviderConfigs contains configuration for all storage providers This is structured to allow easy extension for additional providers in the future


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled indicates if this provider is enabled||
|**ensureavailable**|`boolean`|EnsureAvailable enforces provider availability before completing server startup||
|**region**|`string`|Region for cloud providers||
|**bucket**|`string`|Bucket name for cloud providers||
|**endpoint**|`string`|Endpoint for custom endpoints||
|**proxypresignenabled**|`boolean`|ProxyPresignEnabled toggles proxy-signed download URL generation||
|**baseurl**|`string`|BaseURL is the prefix for proxy download URLs (e.g., http://localhost:17608/v1/files).||
|[**credentials**](#objectstorageprovidersr2credentials)|`object`|ProviderCredentials contains credentials for a storage provider||

**Additional Properties:** not allowed  
**Example**

```json
{
    "credentials": {}
}
```

<a name="objectstorageprovidersr2credentials"></a>
##### objectstorage\.providers\.r2\.credentials: object

ProviderCredentials contains credentials for a storage provider


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**accesskeyid**|`string`|AccessKeyID for cloud providers||
|**secretaccesskey**|`string`|SecretAccessKey for cloud providers||
|**projectid**|`string`|ProjectID for GCS||
|**accountid**|`string`|AccountID for Cloudflare R2||
|**apitoken**|`string`|APIToken for Cloudflare R2||

**Additional Properties:** not allowed  
<a name="objectstorageprovidersdisk"></a>
#### objectstorage\.providers\.disk: object

ProviderConfigs contains configuration for all storage providers This is structured to allow easy extension for additional providers in the future


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled indicates if this provider is enabled||
|**ensureavailable**|`boolean`|EnsureAvailable enforces provider availability before completing server startup||
|**region**|`string`|Region for cloud providers||
|**bucket**|`string`|Bucket name for cloud providers||
|**endpoint**|`string`|Endpoint for custom endpoints||
|**proxypresignenabled**|`boolean`|ProxyPresignEnabled toggles proxy-signed download URL generation||
|**baseurl**|`string`|BaseURL is the prefix for proxy download URLs (e.g., http://localhost:17608/v1/files).||
|[**credentials**](#objectstorageprovidersdiskcredentials)|`object`|ProviderCredentials contains credentials for a storage provider||

**Additional Properties:** not allowed  
**Example**

```json
{
    "credentials": {}
}
```

<a name="objectstorageprovidersdiskcredentials"></a>
##### objectstorage\.providers\.disk\.credentials: object

ProviderCredentials contains credentials for a storage provider


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**accesskeyid**|`string`|AccessKeyID for cloud providers||
|**secretaccesskey**|`string`|SecretAccessKey for cloud providers||
|**projectid**|`string`|ProjectID for GCS||
|**accountid**|`string`|AccountID for Cloudflare R2||
|**apitoken**|`string`|APIToken for Cloudflare R2||

**Additional Properties:** not allowed  
<a name="objectstorageprovidersdatabase"></a>
#### objectstorage\.providers\.database: object

ProviderConfigs contains configuration for all storage providers This is structured to allow easy extension for additional providers in the future


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled indicates if this provider is enabled||
|**ensureavailable**|`boolean`|EnsureAvailable enforces provider availability before completing server startup||
|**region**|`string`|Region for cloud providers||
|**bucket**|`string`|Bucket name for cloud providers||
|**endpoint**|`string`|Endpoint for custom endpoints||
|**proxypresignenabled**|`boolean`|ProxyPresignEnabled toggles proxy-signed download URL generation||
|**baseurl**|`string`|BaseURL is the prefix for proxy download URLs (e.g., http://localhost:17608/v1/files).||
|[**credentials**](#objectstorageprovidersdatabasecredentials)|`object`|ProviderCredentials contains credentials for a storage provider||

**Additional Properties:** not allowed  
**Example**

```json
{
    "credentials": {}
}
```

<a name="objectstorageprovidersdatabasecredentials"></a>
##### objectstorage\.providers\.database\.credentials: object

ProviderCredentials contains credentials for a storage provider


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**accesskeyid**|`string`|AccessKeyID for cloud providers||
|**secretaccesskey**|`string`|SecretAccessKey for cloud providers||
|**projectid**|`string`|ProjectID for GCS||
|**accountid**|`string`|AccountID for Cloudflare R2||
|**apitoken**|`string`|APIToken for Cloudflare R2||

**Additional Properties:** not allowed  
<a name="subscription"></a>