Feature: Unified kairix container
  As an operator deploying via docker compose
  I want one container running both api and worker
  So that operational complexity is one unit, not two

  @current
  Scenario: docker compose up brings up 2 services (was 3)
    When the operator runs `docker compose up -d`
    Then `docker compose ps` lists exactly 2 services: kairix, neo4j
    And the kairix service status is healthy
    And the neo4j service status is healthy

  @current
  Scenario: kairix container runs both api + worker via s6
    Given the kairix container is up
    When the operator runs `docker exec kairix-1 ps -ef`
    Then the process list contains the s6 supervisor as pid 1
    And the process list contains a `kairix mcp serve` process
    And the process list contains a `kairix worker run` process

  @current
  Scenario: Container runs as the kairix user (uid 995)
    Given the kairix container is up
    When the operator runs `docker exec kairix-1 id`
    Then the output reports uid=995
    And the output reports gid=985

  @current
  Scenario: Files written to the volume land as kairix:kairix on host
    Given the kairix container is up with a host bind-mounted /var/lib/kairix
    When the kairix worker writes embeddings to /var/lib/kairix/cache
    Then the file is owned by uid=995 gid=985 on the host

  @current
  Scenario: SIGTERM to the container shuts both processes gracefully
    Given both api and worker are healthy
    When the operator runs `docker stop kairix-1`
    Then both processes receive SIGTERM
    And both exit with code 0
    And the container stops within 30s
