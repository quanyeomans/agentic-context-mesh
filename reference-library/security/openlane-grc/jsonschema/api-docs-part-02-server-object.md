## server: object

Server settings for the echo server


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**dev**|`boolean`|Dev enables echo's dev mode options|no|
|**listen**|`string`|Listen sets the listen address to serve the echo server on|yes|
|**metricsport**|`string`|MetricsPort sets the port for the metrics endpoint|no|
|**shutdowngraceperiod**|`integer`|ShutdownGracePeriod sets the grace period for in flight requests before shutting down|no|
|**readtimeout**|`integer`|ReadTimeout sets the maximum duration for reading the entire request including the body|no|
|**writetimeout**|`integer`|WriteTimeout sets the maximum duration before timing out writes of the response|no|
|**idletimeout**|`integer`|IdleTimeout sets the maximum amount of time to wait for the next request when keep-alives are enabled|no|
|**readheadertimeout**|`integer`|ReadHeaderTimeout sets the amount of time allowed to read request headers|no|
|[**tls**](#servertls)|`object`|TLS settings for the server for secure connections|no|
|[**cors**](#servercors)|`object`|Config holds the cors configuration settings|no|
|[**secure**](#serversecure)|`object`|Config contains the types used in the mw middleware|no|
|[**cachecontrol**](#servercachecontrol)|`object`|Config is the config values for the cache-control middleware|no|
|[**mime**](#servermime)|`object`|Config defines the config for Mime middleware|no|
|[**graphpool**](#servergraphpool)|`object`|PoolConfig contains the settings for the goroutine pool|no|
|**enablegraphextensions**|`boolean`|EnableGraphExtensions enables the graph extensions for the graph resolvers|no|
|**enablegraphsubscriptions**|`boolean`|EnableGraphSubscriptions enables graphql subscriptions to the server using websockets or sse|no|
|**complexitylimit**|`integer`|ComplexityLimit sets the maximum complexity allowed for a query|no|
|**maxresultlimit**|`integer`|MaxResultLimit sets the maximum number of results allowed for a query|no|
|[**csrfprotection**](#servercsrfprotection)|`object`|Config defines configuration for the CSRF middleware wrapper.|no|
|**secretmanager**|`string`|SecretManagerSecret is the name of the GCP Secret Manager secret containing the JWT signing key|no|
|**defaulttrustcenterdomain**|`string`|DefaultTrustCenterDomain is the default domain to use for the trust center if no custom domain is set|no|
|**trustcentercnametarget**|`string`|TrustCenterCnameTarget is the cname target for the trust centerUsed for mapping the vanity domains to the trust centers|no|
|**trustcenterpreviewzoneid**|`string`|TrustCenterPreviewZoneID is the cloudflare zone id for the trust center preview domain|no|
|**notificationlookbackdays**|`integer`|NotificationLookbackDays is the number of days of read notifications to pull when starting a notification subscriptionUnread notifications are always pulled regardless of this setting|no|

**Additional Properties:** not allowed  
**Example**

```json
{
    "tls": {},
    "cors": {
        "prefixes": {}
    },
    "secure": {},
    "cachecontrol": {
        "nocacheheaders": {}
    },
    "mime": {},
    "graphpool": {},
    "csrfprotection": {}
}
```

<a name="servertls"></a>
### server\.tls: object

TLS settings for the server for secure connections


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled turns on TLS settings for the server||
|**certfile**|`string`|CertFile location for the TLS server||
|**certkey**|`string`|CertKey file location for the TLS server||
|**autocert**|`boolean`|AutoCert generates the cert with letsencrypt, this does not work on localhost||

**Additional Properties:** not allowed  
<a name="servercors"></a>
### server\.cors: object

Config holds the cors configuration settings


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enable or disable the CORS middleware||
|[**prefixes**](#servercorsprefixes)|`object`|||
|[**alloworigins**](#servercorsalloworigins)|`string[]`|||
|**cookieinsecure**|`boolean`|CookieInsecure sets the cookie to be insecure||

**Additional Properties:** not allowed  
**Example**

```json
{
    "prefixes": {}
}
```

<a name="servercorsprefixes"></a>
#### server\.cors\.prefixes: object

**Additional Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|[**Additional Properties**](#servercorsprefixesadditionalproperties)|`string[]`|||

<a name="servercorsprefixesadditionalproperties"></a>
##### server\.cors\.prefixes\.additionalProperties: array

**Items**

**Item Type:** `string`  
<a name="servercorsalloworigins"></a>
#### server\.cors\.alloworigins: array

**Items**

**Item Type:** `string`  
<a name="serversecure"></a>
### server\.secure: object

Config contains the types used in the mw middleware


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled indicates if the secure middleware should be enabled||
|**xssprotection**|`string`|XSSProtection is the value to set the X-XSS-Protection header to - default is 1; mode=block||
|**contenttypenosniff**|`string`|ContentTypeNosniff is the value to set the X-Content-Type-Options header to - default is nosniff||
|**xframeoptions**|`string`|XFrameOptions is the value to set the X-Frame-Options header to - default is SAMEORIGIN||
|**hstspreloadenabled**|`boolean`|HSTSPreloadEnabled is a boolean to enable HSTS preloading - default is false||
|**hstsmaxage**|`integer`|HSTSMaxAge is the max age to set the HSTS header to - default is 31536000||
|**contentsecuritypolicy**|`string`|ContentSecurityPolicy is the value to set the Content-Security-Policy header to - default is default-src 'self'||
|**referrerpolicy**|`string`|ReferrerPolicy is the value to set the Referrer-Policy header to - default is same-origin||
|**cspreportonly**|`boolean`|CSPReportOnly is a boolean to enable the Content-Security-Policy-Report-Only header - default is false||

**Additional Properties:** not allowed  
<a name="servercachecontrol"></a>
### server\.cachecontrol: object

Config is the config values for the cache-control middleware


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|||
|[**nocacheheaders**](#servercachecontrolnocacheheaders)|`object`|||
|[**etagheaders**](#servercachecontroletagheaders)|`string[]`|||

**Additional Properties:** not allowed  
**Example**

```json
{
    "nocacheheaders": {}
}
```

<a name="servercachecontrolnocacheheaders"></a>
#### server\.cachecontrol\.nocacheheaders: object

**Additional Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**Additional Properties**|`string`|||

<a name="servercachecontroletagheaders"></a>
#### server\.cachecontrol\.etagheaders: array

**Items**

**Item Type:** `string`  
<a name="servermime"></a>
### server\.mime: object

Config defines the config for Mime middleware


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled indicates if the mime middleware should be enabled||
|**mimetypesfile**|`string`|MimeTypesFile is the file to load mime types from||
|**defaultcontenttype**|`string`|DefaultContentType is the default content type to set if no mime type is found||

**Additional Properties:** not allowed  
<a name="servergraphpool"></a>
### server\.graphpool: object

PoolConfig contains the settings for the goroutine pool


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**maxworkers**|`integer`|MaxWorkers is the maximum number of workers in the pool||

**Additional Properties:** not allowed  
<a name="servercsrfprotection"></a>
### server\.csrfprotection: object

Config defines configuration for the CSRF middleware wrapper.


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled indicates whether CSRF protection is enabled.||
|**header**|`string`|Header specifies the header name to look for the CSRF token.||
|**cookie**|`string`|Cookie specifies the cookie name used to store the CSRF token.||
|**secure**|`boolean`|Secure sets the Secure flag on the CSRF cookie.||
|**samesite**|`string`|SameSite configures the SameSite attribute on the CSRF cookie. Validvalues are "Lax", "Strict", "None" and "Default".||
|**cookiehttponly**|`boolean`|CookieHTTPOnly indicates whether the CSRF cookie is HTTP only.||
|**cookiedomain**|`string`|CookieDomain specifies the domain for the CSRF cookie, default to no domain||
|**cookiepath**|`string`|CookiePath specifies the path for the CSRF cookie, default to "/"||

**Additional Properties:** not allowed  
<a name="entconfig"></a>