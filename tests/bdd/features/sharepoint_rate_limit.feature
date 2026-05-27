@connector @sharepoint @rate_limit
Feature: SharePoint connector recovers from Microsoft Graph throttling
  As an operator running kairix against a Microsoft 365 tenant
  I want a throttled Graph response to back off and retry instead of dead-lettering every item
  So that an outage spike on Graph's side doesn't blank my entire SharePoint sync.

  Microsoft Graph returns 429 (Too Many Requests) or 503 (Service
  Unavailable) when the per-tenant request budget is exhausted, with a
  Retry-After header (seconds) indicating how long to wait before the
  next attempt. The SharePoint Graph client honours that header rather
  than raising immediately and propagating an httpx.HTTPStatusError up
  to the per-item dispatch loop, which previously dead-lettered every
  item on the throttled drive.

  @happy_path
  Scenario: Graph throttles once with Retry-After and the client recovers cleanly
    Given a stubbed Microsoft Graph endpoint that returns 429 with Retry-After 2 once then 200 with a sample pdf envelope in /Curated-Content
    When the operator runs the sharepoint connector list_changes against the throttled graph stub
    Then one created change event is emitted for the recovered drive
    And the throttling sleep budget recorded is 2 seconds
