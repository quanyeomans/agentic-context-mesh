## campaignwebhook: object

CampaignWebhookConfig contains webhook configuration for campaign-related email providers.


**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled toggles the campaign webhook handler||
|**resendapikey**|`string`|ResendAPIKey is the API key used for Resend client initialization||
|**resendsecret**|`string`|ResendSecret is the signing secret used to verify Resend webhook payloads||

**Additional Properties:** not allowed  
<a name="cloudflare"></a>