## ratelimit: object

Config defines the configuration settings for the rate limiter middleware.


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|||
|[**options**](#ratelimitoptions)|`array`|||
|[**headers**](#ratelimitheaders)|`string[]`|||
|**forwardedindexfrombehind**|`integer`|ForwardedIndexFromBehind selects which IP from X-Forwarded-For should be used.0 means the closest client, 1 the proxy behind it, etc.||
|**includepath**|`boolean`|IncludePath appends the request path to the limiter key when true.||
|**includemethod**|`boolean`|IncludeMethod appends the request method to the limiter key when true.||
|**keyprefix**|`string`|KeyPrefix allows scoping the limiter key space with a static prefix.||
|**denystatus**|`integer`|DenyStatus overrides the HTTP status code returned when a rate limit is exceeded.||
|**denymessage**|`string`|DenyMessage customises the error payload when a rate limit is exceeded.||
|**sendretryafterheader**|`boolean`|SendRetryAfterHeader toggles whether the Retry-After header should be added when available.||
|**dryrun**|`boolean`|DryRun enables logging rate limit decisions without blocking requests.||

**Additional Properties:** not allowed  
**Example**

```json
{
    "options": [
        {}
    ]
}
```

<a name="ratelimitoptions"></a>
### ratelimit\.options: array

**Items**

**Example**

```json
[
    {}
]
```

<a name="ratelimitheaders"></a>
### ratelimit\.headers: array

**Items**

**Item Type:** `string`  
<a name="objectstorage"></a>