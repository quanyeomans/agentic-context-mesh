# Choosing the Appropriate Tool

- **Default to replace_in_file** for most changes. It's the safer, more precise option that minimizes potential issues.
- **Use create_file** when:
  - Creating new files
  - The changes are so extensive that using replace_in_file would be more complex or risky
  - You need to completely reorganize or restructure a file
  - The file is relatively small and the changes affect most of its content
  - You're generating boilerplate or template files