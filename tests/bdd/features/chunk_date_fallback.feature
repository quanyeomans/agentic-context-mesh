Feature: All documents are searchable by date
  As a user who wants to find what changed recently
  I need every document to have a date — even if the author didn't add one
  So that date-based searches always include all relevant documents

  Scenario: Documents with a date field use that date
    Given a document with frontmatter date "2026-04-15"
    When chunk date is extracted
    Then the chunk date is "2026-04-15"

  Scenario: Documents without a date field still get a date from when the file was last changed
    Given a document with no frontmatter date and file mtime "2026-03-20"
    When chunk date is extracted with document root
    Then the chunk date is "2026-03-20"

  Scenario: A date in the filename is preferred over the file modification date
    Given a document at path "notes/2026-04-01-meeting.md" with file mtime "2026-03-20"
    When chunk date is extracted
    Then the chunk date is "2026-04-01"
