---
title: "Google Sheets"
source: dbt Core Documentation
source_url: https://github.com/dbt-labs/docs.getdbt.com
licence: Apache-2.0
domain: data-and-analysis
subdomain: dbt-docs
date_added: 2026-04-25
---

# Google Sheets <Lifecycle status="self_service,managed,managed_plus" />

The <Constant name="semantic_layer" /> offers a seamless integration with Google Sheets through a custom menu. This add-on allows you to build <Constant name="semantic_layer" /> queries and return data on your metrics directly within Google Sheets

## Prerequisites

- You have [configured the <Constant name="semantic_layer" />](/docs/use-dbt-semantic-layer/setup-sl) and are using dbt v1.6 or higher.
- You need a Google account with access to Google Sheets and the ability to install Google add-ons.
- You have a [<Constant name="dbt" /> Environment ID](/docs/use-dbt-semantic-layer/setup-sl#set-up-dbt-semantic-layer).
- You have a [service token](/docs/dbt-cloud-apis/service-tokens) or a [personal access token](/docs/dbt-cloud-apis/user-tokens) to authenticate with from a <Constant name="dbt" /> account.
- You must have a <Constant name="dbt" /> Starter or Enterprise-tier [account](https://www.getdbt.com/pricing). Suitable for both Multi-tenant and Single-tenant deployment.

If you're using [IP restrictions](/docs/cloud/secure/ip-restrictions), ensure you've added [Google’s IP addresses](https://www.gstatic.com/ipranges/goog.txt) to your IP allowlist. Otherwise, the Google Sheets connection will fail.

import SLCourses from '/snippets/_sl-course.md';

<SLCourses/>

## Installing the add-on

1. Navigate to the [<Constant name="semantic_layer" /> for Sheets App](https://gsuite.google.com/marketplace/app/foo/392263010968) to install the add-on. You can also find it in Google Sheets by going to [**Extensions -> Add-on -> Get add-ons**](https://support.google.com/docs/answer/2942256?hl=en&co=GENIE.Platform%3DDesktop&oco=0#zippy=%2Cinstall-add-ons%2Cinstall-an-add-on) and searching for it there.
2. After installing, open the **Extensions** menu and select **<Constant name="semantic_layer" /> for Sheets**. This will open a custom menu on the right-hand side of your screen.
3. [Find your](/docs/use-dbt-semantic-layer/setup-sl#set-up-dbt-semantic-layer) **Host** and **Environment ID** in <Constant name="dbt" />.
   - Navigate to **Account Settings** and select **Projects** on the left sidebar.
   - Select your project and then navigate to the **<Constant name="semantic_layer" />** settings.  You'll need this to authenticate in Google Sheets in the following step.
   - You can generate your service token by clicking **Generate service token** within the <Constant name="semantic_layer" /> configuration page or navigating to **API tokens** in <Constant name="dbt" />. Alternatively, you can also create a personal access token by going to **API tokens** > **Personal tokens**. 
      <Lightbox src="/img/docs/dbt-cloud/semantic-layer/sl-and-gsheets.png" width="70%" title="Access your Environment ID, Host, and URLs in your dbt Semantic Layer settings. Generate a service token in the Semantic Layer settings or API tokens settings" />
4. In Google Sheets, authenticate with your Host, <Constant name="dbt" /> Environment ID, and service or personal token.

5. Start querying your metrics using the **Query Builder**. For more info on the menu functions, refer to [Query Builder functions](#query-builder-functions). To cancel a query while running, press the "Cancel" button.

import Tools from '/snippets/_sl-excel-gsheets.md';

<Tools 
type="Google Sheets"
bullet_1="The custom menu operation has a timeout limit of six (6) minutes."
bullet_2="If you're using this extension, make sure you're signed into Chrome with the same Google profile you used to set up the Add-On. Log in with one Google profile at a time as using multiple Google profiles at once might cause issues."
bullet_3="Note that only standard granularities are currently available, custom time granularities aren't currently supported for this integration."
queryBuilder="/img/docs/dbt-cloud/semantic-layer/query-builder.png"
PrivateSelections="You can also make these selections private or public. Public selections mean your inputs are available in the menu to everyone on the sheet. 
Private selections mean your inputs are only visible to you. Note that anyone added to the sheet can still see the data from these private selections, but they won't be able to interact with the selection in the menu or benefit from the automatic refresh."
/>


**Limited use policy disclosure**

The <Constant name="semantic_layer" /> for Sheet's use and transfer to any other app of information received from Google APIs will adhere to [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

## FAQs
<FAQ path="Troubleshooting/sl-alpn-error" />
