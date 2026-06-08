@retrieval
Feature: Reciprocal Rank Fusion with asymmetric-list normalisation
  As a kairix retrieval pipeline
  I want fusion to compensate for asymmetric BM25 and vector list lengths
  So that one list doesn't silently outweigh the other when limits differ (Issue #454)

  Scenario: Symmetric limits behave like classic Cormack 2009 RRF
    Given a BM25 result list of length 3
    And a vector result list of length 3
    When I fuse the two lists with RRF
    Then the per-document rrf_score equals the unnormalised Cormack 2009 score within float epsilon

  Scenario: Asymmetric limits weight the shorter list appropriately
    Given a BM25 result list of length 1 containing only "/bm25-top.md"
    And a vector result list of length 5 with "/vec-top.md" at rank 1
    When I fuse the two lists with RRF
    Then "/vec-top.md" ranks above "/bm25-top.md"
    And "/vec-top.md" is the top result

  Scenario: Document in shorter list only is not penalised by absent tail
    Given a BM25 result list of length 1 containing only "/bm25only.md"
    And a vector result list of length 5 with five distinct documents
    When I fuse the two lists with RRF
    Then "/bm25only.md" has a non-zero rrf_score
    And the rrf_score equals 0.2 multiplied by 1 over (k plus 1)
