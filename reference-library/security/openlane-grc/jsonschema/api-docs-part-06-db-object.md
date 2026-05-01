## db: object

**Properties**

|Name|Type|Description|Required|
|----|----|-----------|--------|
|**debug**|`boolean`|debug enables printing the debug database logs|no|
|**databasename**|`string`|the name of the database to use with otel tracing|no|
|**drivername**|`string`|sql driver name|no|
|**multiwrite**|`boolean`|enables writing to two databases simultaneously|no|
|**primarydbsource**|`string`|dsn of the primary database|yes|
|**secondarydbsource**|`string`|dsn of the secondary database if multi-write is enabled|no|
|**cachettl**|`integer`|cache results for subsequent requests|no|
|**runmigrations**|`boolean`|run migrations on startup|no|
|**migrationprovider**|`string`|migration provider to use for running migrations|no|
|**enablehistory**|`boolean`|enable history data to be logged to the database|no|
|**maxconnections**|`integer`|maximum number of connections to the database|no|
|**maxidleconnections**|`integer`|maximum number of idle connections to the database|no|

**Additional Properties:** not allowed  
<a name="jobqueue"></a>