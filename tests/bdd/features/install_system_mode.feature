# F45-anchor: this feature is the BDD spec for the `kairix init --system`
# AND `kairix uninstall --system` CLI subcommands (both register their
# # F45-feature: override pointer in kairix/cli.py to this file).
Feature: kairix init --system
  As an operator deploying kairix to a fresh Linux host
  I want `sudo kairix init --system` to create a complete working install
  So that I don't need to know about systemd, users, or FHS paths

  Background:
    Given a clean test root simulating a fresh host

  @future
  Scenario: First run creates user, dirs, config, systemd unit
    When the operator runs `kairix init --system --prefix <test_root>` as simulated root
    Then a kairix system user exists with uid >= 990
    And /etc/kairix/kairix.config.yaml exists with mode 0644
    And /var/lib/kairix/ exists owned by kairix:kairix
    And /var/cache/kairix/ exists owned by kairix:kairix
    And /etc/systemd/system/kairix.service exists with mode 0644
    And the systemd unit declares User=kairix
    And `systemctl status kairix` reports the unit as enabled

  @future
  Scenario: Re-running is a no-op
    Given `kairix init --system` has already run successfully
    When the operator runs `kairix init --system` again
    Then exit code is 0
    And no warnings about existing-file conflicts
    And the install report shows action=unchanged for every step

  @future
  Scenario: Refusing to run as non-root with --system
    When a non-root user runs `kairix init --system`
    Then exit code is 1
    And stderr says "system-mode install requires root; re-run with sudo OR pass --user"

  @future
  Scenario: `kairix init verify` reports install health
    Given `kairix init --system` has run successfully
    When the operator runs `kairix init verify`
    Then exit code is 0
    And stdout lists every install element marked OK

  @future
  Scenario: `kairix uninstall --system` removes everything except data
    Given `kairix init --system` has run successfully
    And /var/lib/kairix/index.sqlite exists with operator data
    When the operator runs `kairix uninstall --system --keep-data`
    Then /etc/kairix/ is removed
    And /etc/systemd/system/kairix.service is removed
    And the kairix system user is removed
    And /var/lib/kairix/index.sqlite STILL exists
