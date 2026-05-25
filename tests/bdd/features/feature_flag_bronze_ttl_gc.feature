@feature_flag @bronze_ttl_gc
Feature: bronze_ttl_gc feature flag bounds bronze growth long-term
  As an operator running a large connector corpus (#316)
  I want bronze raw blobs to expire after a TTL when the flag is ON
  So that bronze storage does not grow unbounded as the corpus churns

  When the flag is OFF (default), the maintenance scheduler's bronze
  TTL GC stage is a structural no-op: it never touches disk or DB.
  When ON, the stage deletes every bronze_records row plus on-disk
  raw blob whose fetched_at is older than KAIRIX_BRONZE_TTL_DAYS
  (default 7).

  @happy_path @off
  Scenario: flag default-off — bronze TTL GC stage is a no-op
    Given the operator has the bronze-ttl-gc flag set to false
    And the bronze store contains a backdated registered blob for "alpha-source"
    When the maintenance scheduler runs its bronze TTL GC stage
    Then no bronze_records rows are deleted
    And the backdated blob is still on disk

  @on
  Scenario: flag effective-true — bronze TTL GC deletes blobs older than the TTL
    Given the operator has the bronze-ttl-gc flag set to true
    And the bronze store contains a backdated registered blob for "alpha-source"
    When the maintenance scheduler runs its bronze TTL GC stage
    Then the backdated bronze_records row is deleted
    And the backdated blob is removed from disk
