## entconfig: object

Config holds the configuration for the ent server


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|[**entitytypes**](#entconfigentitytypes)|`string[]`|||
|[**summarizer**](#entconfigsummarizer)|`object`|Config holds configuration for the text summarization functionality||
|**maxpoolsize**|`integer`|MaxPoolSize is the max worker pool size that can be used by the ent client||
|[**modules**](#entconfigmodules)|`object`|Modules settings for features access||
|**maxschemaimportsize**|`integer`|MaxSchemaImportSize is the maximum size allowed for schema imports in bytes||
|[**emailvalidation**](#entconfigemailvalidation)|`object`|EmailVerificationConfig is the configuration for email verification||
|[**billing**](#entconfigbilling)|`object`|Billing settings for feature access||
|[**notifications**](#entconfignotifications)|`object`|Notifications settings for notifications sent to users based on events||

**Additional Properties:** not allowed  
**Example**

```json
{
    "summarizer": {
        "llm": {
            "anthropic": {},
            "cloudflare": {},
            "openai": {}
        }
    },
    "modules": {},
    "emailvalidation": {
        "allowedemailtypes": {}
    },
    "billing": {},
    "notifications": {}
}
```

<a name="entconfigentitytypes"></a>
### entconfig\.entitytypes: array

**Items**

**Item Type:** `string`  
<a name="entconfigsummarizer"></a>
### entconfig\.summarizer: object

Config holds configuration for the text summarization functionality


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**type**|`string`|Type specifies the summarization algorithm to use||
|[**llm**](#entconfigsummarizerllm)|`object`|LLM contains configuration for multiple LLM providers||
|**maximumsentences**|`integer`|MaximumSentences specifies the maximum number of sentences in the summary||

**Additional Properties:** not allowed  
**Example**

```json
{
    "llm": {
        "anthropic": {},
        "cloudflare": {},
        "openai": {}
    }
}
```

<a name="entconfigsummarizerllm"></a>
#### entconfig\.summarizer\.llm: object

LLM contains configuration for multiple LLM providers


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**provider**|`string`|Provider specifies which LLM service to use||
|[**anthropic**](#entconfigsummarizerllmanthropic)|`object`|AnthropicConfig contains Anthropic specific configuration||
|[**cloudflare**](#entconfigsummarizerllmcloudflare)|`object`|CloudflareConfig contains Cloudflare specific configuration||
|[**openai**](#entconfigsummarizerllmopenai)|`object`|OpenAIConfig contains OpenAI specific configuration||

**Additional Properties:** not allowed  
**Example**

```json
{
    "anthropic": {},
    "cloudflare": {},
    "openai": {}
}
```

<a name="entconfigsummarizerllmanthropic"></a>
##### entconfig\.summarizer\.llm\.anthropic: object

AnthropicConfig contains Anthropic specific configuration


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**betaheader**|`string`|BetaHeader specifies the beta API features to enable||
|**legacytextcompletion**|`boolean`|LegacyTextCompletion enables legacy text completion API||
|**baseurl**|`string`|BaseURL specifies the API endpoint||
|**model**|`string`|Model specifies the model name to use||
|**apikey**|`string`|APIKey contains the authentication key for the service||

**Additional Properties:** not allowed  
<a name="entconfigsummarizerllmcloudflare"></a>
##### entconfig\.summarizer\.llm\.cloudflare: object

CloudflareConfig contains Cloudflare specific configuration


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**model**|`string`|Model specifies the model name to use||
|**apikey**|`string`|APIKey contains the authentication key for the service||
|**accountid**|`string`|AccountID specifies the Cloudflare account ID||
|**serverurl**|`string`|ServerURL specifies the API endpoint||

**Additional Properties:** not allowed  
<a name="entconfigsummarizerllmopenai"></a>
##### entconfig\.summarizer\.llm\.openai: object

OpenAIConfig contains OpenAI specific configuration


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**model**|`string`|Model specifies the model name to use||
|**apikey**|`string`|APIKey contains the authentication key for the service||
|**url**|`string`|URL specifies the API endpoint||
|**organizationid**|`string`|OrganizationID specifies the OpenAI organization ID||

**Additional Properties:** not allowed  
<a name="entconfigmodules"></a>
### entconfig\.modules: object

Modules settings for features access


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled indicates whether to check and verify module access||
|**usesandbox**|`boolean`|UseSandbox indicates whether to use the sandbox catalog for module access checks||
|**devmode**|`boolean`|DevMode enables all modules for local development regardless of trial status||

**Additional Properties:** not allowed  
<a name="entconfigemailvalidation"></a>
### entconfig\.emailvalidation: object

EmailVerificationConfig is the configuration for email verification


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled indicates whether email verification is enabled||
|**enableautoupdatedisposable**|`boolean`|EnableAutoUpdateDisposable indicates whether to automatically update disposable email addresses||
|**enablegravatarcheck**|`boolean`|EnableGravatarCheck indicates whether to check for Gravatar existence||
|**enablesmtpcheck**|`boolean`|EnableSMTPCheck indicates whether to check email by smtp||
|[**allowedemailtypes**](#entconfigemailvalidationallowedemailtypes)|`object`|AllowedEmailTypes defines the allowed email types for verification||

**Additional Properties:** not allowed  
**Example**

```json
{
    "allowedemailtypes": {}
}
```

<a name="entconfigemailvalidationallowedemailtypes"></a>
#### entconfig\.emailvalidation\.allowedemailtypes: object

AllowedEmailTypes defines the allowed email types for verification


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**disposable**|`boolean`|Disposable indicates whether disposable email addresses are allowed||
|**free**|`boolean`|Free indicates whether free email addresses are allowed||
|**role**|`boolean`|Role indicates whether role-based email addresses are allowed||

**Additional Properties:** not allowed  
<a name="entconfigbilling"></a>
### entconfig\.billing: object

Billing settings for feature access


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**requirepaymentmethod**|`boolean`|RequirePaymentMethod indicates whether to check if a payment methodexists for orgs before they can access some resource||
|[**bypassemaildomains**](#entconfigbillingbypassemaildomains)|`string[]`|||

**Additional Properties:** not allowed  
<a name="entconfigbillingbypassemaildomains"></a>
#### entconfig\.billing\.bypassemaildomains: array

**Items**

**Item Type:** `string`  
<a name="entconfignotifications"></a>
### entconfig\.notifications: object

Notifications settings for notifications sent to users based on events


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**consoleurl**|`string`|ConsoleURL for ui links used in notifications||

**Additional Properties:** not allowed  
<a name="auth"></a>