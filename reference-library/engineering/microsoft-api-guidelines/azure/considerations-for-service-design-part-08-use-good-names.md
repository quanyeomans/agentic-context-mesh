## Use Good Names

Good names for resources, properties, operations, and parameters are essential to a great developer experience.

Resources are described by nouns. Resource and property names must be descriptive and easy for customers to understand.
Use names that correspond to user scenarios rather than service implementation details, e.g. "Diagnosis" and not "TreeLeafNode".
Names should convey the value's purpose and not simply describe its structure, e.g. "ConfigurationSetting" and not "KeyValuePair".
Ease of understanding comes from familiarity and recognition; you should favor consistency with other Azure services, names in the product's portal/user interface, and industry standards.

Names should aid developers in discovering functionality without having to constantly refer to documentation.
Use common patterns and standard conventions to aid developers in correctly guessing common property names and meanings.
Use verbose naming patterns and avoid abbreviations other than
well-known acronyms in your service domain.

[:white_check_mark:](#naming-consistency) **DO** use the same name for the same concept and different names for different concepts wherever possible.

### Recommended Naming Conventions

The following are recommended naming conventions for Azure services:

[:white_check_mark:](#naming-collections) **DO** name collections as plural nouns or plural noun phrases using correct English.

[:white_check_mark:](#naming-values) **DO** name values that are not collections as singular nouns or singular noun phrases.

[:ballot_box_with_check:](#naming-adjective-before-noun) **YOU SHOULD** should place the adjective before the noun in names that contain both a noun and an adjective.

For example, `collectedItems` not `itemsCollected`

[:ballot_box_with_check:](#naming-acronym-case) **YOU SHOULD** case all acronyms as though they were regular words (i.e. lower camelCase).

For example, `nextUrl` not `nextURL`.

[:ballot_box_with_check:](#naming-date-time) **YOU SHOULD** use an "At" suffix in names of `date-time` values.

For example, `createdAt` not `created` or `createdDateTime`.

[:ballot_box_with_check:](#naming-include-units) **YOU SHOULD** use a suffix of the unit of measurement for values with a clear unit of measurement (such as bytes, miles, and so on). Use a generally accepted abbreviation for the units (e.g. "Km" rather than "Kilometers") when appropriate.

[:ballot_box_with_check:](#naming-duration) **YOU SHOULD** use an int for time durations and include the time units in the name.

For example, `expirationDays` as `int` and not `expiration` as `date-time`.

[:warning:](#naming-brand-names) **YOU SHOULD NOT** use brand names in resource or property names.

[:warning:](#naming-avoid-acronyms) **YOU SHOULD NOT** use acronyms or abbreviations unless they are broadly understood for example, "ID" or "URL", but not "Num" for "number".

[:warning:](#naming-avoid-reserved-words) **YOU SHOULD NOT** use names that are reserved words in widely used programming languages (including C#, Java, JavaScript/TypeScript, Python, C++, and Go).

[:no_entry:](#naming-boolean) **DO NOT** use "is" prefix in names of `boolean` values, e.g. "enabled" not "isEnabled".

[:no_entry:](#naming-avoid-redundancy) **DO NOT** use redundant words in names.

For example, `/phones/number` and not `phone/phoneNumber`.

### Common names

The following are recommended names for properties that match the associated description:

| Name | Description |
|------------- | --- |
| createdAt | The date and time the resource was created. |
| lastModifiedAt | The date and time the resource was last modified. |
| deletedAt | The date and time the resource was deleted. |
| kind   | The discriminator value for a polymorphic resource |
| etag | The entity tag used for optimistic concurrency control, when included as a property of a resource. |

### `name` vs `id`

[:white_check_mark:](#naming-name-vs-id) **DO** use "Id" suffix for the name of the identifier of a resource.

This holds even in the case where the identifier is assigned by the user with a PUT/PATCH method.