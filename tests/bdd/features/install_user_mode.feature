# F45-anchor: this feature is the BDD spec for the `kairix init` CLI subcommand
# (registered in kairix/cli.py with a matching # F45-feature: override pointer).
Feature: kairix init --user
  As a developer or solo user installing kairix to their own home
  I want `kairix init --user` to install without sudo
  So that I can run kairix without admin rights

  Background:
    Given XDG_CONFIG_HOME=/tmp/test-config
    And XDG_DATA_HOME=/tmp/test-data

  @future
  Scenario: First run lays down user-mode install
    When the operator runs `kairix init --user`
    Then /tmp/test-config/kairix/kairix.config.yaml exists
    And /tmp/test-data/kairix/ exists
    And ~/.config/systemd/user/kairix.service exists
    And the systemd unit does NOT declare User=
    And `systemctl --user status kairix` reports enabled

  @future
  Scenario: User-mode refuses to install global systemd unit
    When the operator runs `kairix init --user`
    Then /etc/systemd/system/kairix.service does NOT exist

  @future
  Scenario: System and user installs can coexist on the same host
    Given `sudo kairix init --system` has run
    When a non-root user runs `kairix init --user`
    Then the user-mode install lands under their HOME
    And the system-mode install at /etc/kairix is untouched
