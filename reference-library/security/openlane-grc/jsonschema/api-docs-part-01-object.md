# object

Config contains the configuration for the core server


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**domain**|`string`|Domain provides a global domain value for other modules to inherit||
|**refreshinterval**|`integer`|RefreshInterval determines how often to reload the config||
|[**server**](#server)|`object`|Server settings for the echo server|yes|
|[**entconfig**](#entconfig)|`object`|Config holds the configuration for the ent server||
|[**auth**](#auth)|`object`|Auth settings including oauth2 providers and token configuration|yes|
|[**authz**](#authz)|`object`||yes|
|[**db**](#db)|`object`||yes|
|[**jobqueue**](#jobqueue)|`object`|||
|[**redis**](#redis)|`object`|||
|[**email**](#email)|`object`|||
|[**sessions**](#sessions)|`object`|||
|[**totp**](#totp)|`object`|||
|[**ratelimit**](#ratelimit)|`object`|Config defines the configuration settings for the rate limiter middleware.||
|[**objectstorage**](#objectstorage)|`object`|ProviderConfig contains configuration for object storage providers||
|[**subscription**](#subscription)|`object`|||
|[**keywatcher**](#keywatcher)|`object`|KeyWatcher contains settings for the key watcher that manages JWT signing keys||
|[**slack**](#slack)|`object`|Slack contains settings for Slack notifications||
|[**integrations**](#integrations)|`object`|||
|[**workflows**](#workflows)|`object`|||
|[**campaignwebhook**](#campaignwebhook)|`object`|CampaignWebhookConfig contains webhook configuration for campaign-related email providers.||
|[**cloudflare**](#cloudflare)|`object`|CloudflareConfig contains configuration for Cloudflare integration.||
|[**shortlinks**](#shortlinks)|`object`|||

**Additional Properties:** not allowed  
**Example**

```json
{
    "server": {
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
    },
    "entconfig": {
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
    },
    "auth": {
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
    },
    "authz": {
        "credentials": {}
    },
    "db": {},
    "jobqueue": {
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
    },
    "redis": {},
    "email": {
        "urls": {}
    },
    "sessions": {},
    "totp": {},
    "ratelimit": {
        "options": [
            {}
        ]
    },
    "objectstorage": {
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
    },
    "subscription": {
        "stripewebhooksecrets": {}
    },
    "keywatcher": {},
    "slack": {},
    "integrations": {
        "githubapp": {},
        "slack": {},
        "googleworkspace": {},
        "azureentraid": {},
        "microsoftteams": {},
        "oidclocal": {}
    },
    "workflows": {
        "cel": {},
        "gala": {}
    },
    "campaignwebhook": {},
    "cloudflare": {},
    "shortlinks": {}
}
```

<a name="server"></a>