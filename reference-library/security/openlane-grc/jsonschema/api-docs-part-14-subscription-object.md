## subscription: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**enabled**|`boolean`|Enabled determines if the entitlements service is enabled||
|**privatestripekey**|`string`|PrivateStripeKey is the key for the stripe service||
|**stripewebhooksecret**|`string`|StripeWebhookSecret is the secret for the stripe service (legacy, use StripeWebhookSecrets for version-specific secrets)||
|[**stripewebhooksecrets**](#subscriptionstripewebhooksecrets)|`object`|||
|**stripewebhookurl**|`string`|StripeWebhookURL is the URL for the stripe webhook||
|**stripebillingportalsuccessurl**|`string`|StripeBillingPortalSuccessURL||
|**stripecancellationreturnurl**|`string`|StripeCancellationReturnURL is the URL for the stripe cancellation return||
|[**stripewebhookevents**](#subscriptionstripewebhookevents)|`string[]`|||
|**stripewebhookapiversion**|`string`|StripeWebhookAPIVersion is the Stripe API version currently accepted by the webhook handler||
|**stripewebhookdiscardapiversion**|`string`|StripeWebhookDiscardAPIVersion is the Stripe API version to discard during migration||

**Additional Properties:** not allowed  
**Example**

```json
{
    "stripewebhooksecrets": {}
}
```

<a name="subscriptionstripewebhooksecrets"></a>
### subscription\.stripewebhooksecrets: object

**Additional Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**Additional Properties**|`string`|||

<a name="subscriptionstripewebhookevents"></a>
### subscription\.stripewebhookevents: array

**Items**

**Item Type:** `string`  
<a name="keywatcher"></a>