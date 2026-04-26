## edit_file
Description: Request to edit the contents of a file based on a diff string.
The diff string should be in the following format:
<<<<<<< SEARCH
<STRING_TO_REPLACE>
=======
<STRING_TO_REPLACE_WITH>
>>>>>>> REPLACE

This will search for the content between \`<<<<<<< SEARCH\` and \`=======\`, and replace it with the content between \`=======\` and \`>>>>>>> REPLACE\`. 

Every *to_replace* must *EXACTLY MATCH* the existing source code, character for character, including all comments, empty lines and docstrings (You should escape the special characters as needed in to_replace example - from """ to "\\"\\"\\).

Include enough lines to make code in \`to_replace\` unique. \`to_replace\` should NOT be empty.
\`edit_file\` will only replace the *first* matching occurrence.

For example, given a file "/workspace/example.txt" with the following content:
\`\`\`
line 1
line 2
line 2
line 3
\`\`\`

EDITING: If you want to replace the second occurrence of "line 2", you can make \`to_replace\` unique with a diff string like this:
<edit_file>
<path>/workspace/example.txt</path>
<content>
<<<<<<< SEARCH
line 2
line 3
=======
new line
line 3
>>>>>>> REPLACE
</content>
</edit_file>

This will replace only the second "line 2" with "new line". The first "line 2" will remain unchanged.

The resulting file will be:
\`\`\`
line 1
line 2
new line
line 3
\`\`\`

REMOVAL: If you want to remove "line 2" and "line 3", you can set \`new_content\` to an empty string:

<edit_file>
<path>/workspace/example.txt</path>
<content>
<<<<<<< SEARCH
line 2
line 3
=======
>>>>>>> REPLACE
</content>
</edit_file>

To do multiple edits to a file:
<edit_file>
<path>/workspace/example.txt</path>
<content>
<<<<<<< SEARCH
<STRING_TO_REPLACE_1>
=======
<STRING_TO_REPLACE_WITH_1>
>>>>>>> REPLACE
<<<<<<< SEARCH
<STRING_TO_REPLACE_2>
=======
<STRING_TO_REPLACE_WITH_2>
>>>>>>> REPLACE
</content>
</edit_file>