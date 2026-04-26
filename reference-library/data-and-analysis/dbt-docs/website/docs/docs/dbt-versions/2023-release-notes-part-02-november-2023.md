## November 2023

- <Expandable alt_header='New features and UI changes to dbt Catalog'>

    There are new quality-of-life improvements in dbt Cloud for email and Slack notifications about your jobs: 

    - You can add external email addresses and send job notifications to them. External emails can be:
        - Addresses that are outside of your dbt Cloud account
        - Third-party integration addresses for configuring notifications to services like Microsoft Teams or PagerDuty 
    - You can configure notifications for multiple Slack channels. Previously, you could only configure one Slack channel. 
    - Any account admin can now edit slack notifications, not just the person who created them. 

    To learn more, check out [Job notifications](/docs/deploy/job-notifications).

  </Expandable>

- <Expandable alt_header='Job notifications'>

    There are new quality-of-life improvements in dbt Cloud for email and Slack notifications about your jobs: 

    - You can add external email addresses and send job notifications to them. External emails can be:
        - Addresses that are outside of your dbt Cloud account
        - Third-party integration addresses for configuring notifications to services like Microsoft Teams or PagerDuty 
    - You can configure notifications for multiple Slack channels. Previously, you could only configure one Slack channel. 
    - Any account admin can now edit slack notifications, not just the person who created them. 

    To learn more, check out [Job notifications](/docs/deploy/job-notifications).

  </Expandable>

- <Expandable alt_header='Repo caching'>

    Now available for dbt Cloud Enterprise plans is a new option to enable Git repository caching for your job runs. When enabled, dbt Cloud caches your dbt project's Git repository and uses the cached copy instead if there's an outage with the Git provider. This feature improves the reliability and stability of your job runs. 

    To learn more, refer to [Repo caching](/docs/cloud/account-settings#git-repository-caching).

    <Lightbox src="/img/docs/deploy/account-settings-repository-caching.png" width="85%" title="Example of the Repository caching option" />

  </Expandable>