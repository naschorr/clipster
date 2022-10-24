# Configuring Clipster
See `config.json` in the Clipster installation's root.

### Discord Configuration
- **name** - String - The bot's name.
- **version** - String - The bot's current semantic version.
- **description** - Array - An array of strings making up the bot's description. Each element in the array goes on a new line in the help interface.
- **channel_timeout_seconds** - Int - The time in seconds before the bot will leave its current voice channel due to inactivity.
- **channel_timeout_clip_paths** - Array - Array of paths (relative to the `modules/clips/clips` directory) that point to clips that can be played when the bot times out of a channel.
- **skip_percentage** - Float - The minimum percentage of other users who need to request a skip before the currently playing audio will be skipped. Must be a floating point number between 0.0 and 1.0 inclusive.
- **repo_url** - String - The URL of the repository that hosts the bot
- **bot_invite_url** - String - The URL used to invite the bot to any Discord servers the user controls.
- **bot_invite_blurb** - String - A string of text that is applied to bot invites, so users can have more context about the bot.
- **support_discord_invite_url** - String - The URL of the invite for the bot's support Discord.
- **privacy_policy_url** - String - The URL of the bot's privacy policy.
- **accent_color_hex** - String - A hex string containing the color code for the bot's accent color. This is used to customize embeds to ensure they're visually consistent with the bot.

### Bot Configuration
- **log_level** - String - The minimum error level to log. Potential values are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`, in order of severity (ascending). For example, choosing the `WARNING` log level will log everything tagged as `WARNING`, `ERROR`, and `CRITICAL`.
- **log_path** - String - The path where logs should be stored. If left empty, it will default to a `logs` folder inside the Clipster root.
- **log_max_bytes** - Int - The maximum number of bytes to store in a log file.
- **log_backup_count** - Int - The maximum number of logs to keep before deleting the oldest ones.
- **discord_token** - String - The token for the bot, used to authenticate with Discord.
- **delete_request_queue_file_path** - String - The path where the delete requests file should be stored. If left empty, it will default to a `privacy/delete_request.txt` file inside the Clipster root.
- **delete_request_meta_file_path** - String - The path where the delete requests metadata file should be stored. For example, this includes the time the delete request queue was last parsed. If left empty, it will default to a `privacy/metadata.json` file inside the Clipster root.
- **delete_request_weekday_to_process** - Integer - The integer corresponding to the day of the week to perform the delete request queue processing. 0 is Monday, 7 is Sunday, and so on.
- **delete_request_time_to_process** - String - The ISO8601 time string that specifies when the queue should be processed, when the provided day comes up each week. Make sure to use the format `THH:MM:SSZ`.
- **modules_dir** - String - The name of the directory, located in Clipster's root, which will contain the modules to dynamically load. See ModuleManager's discover() method for more info about how modules need to be formatted for loading.
- **\_modules_dir_path** - String - The path to the directory that contains the modules to be loaded for the bot. Remove the leading underscore to activate it.
- **string_similarity_algorithm** - String - The name of the algorithm to use when calculating how similar two given strings are. Currently only supports 'difflib'.
- **invalid_command_minimum_similarity** - Float - The minimum similarity an invalid command must have with an existing command before the existing command will be suggested as an alternative.
- **find_command_minimum_similarity** - Float - The minimum similarity the find command must have with an existing command, before the existing command will be suggested for use.
> *A quick note about minimum similarity*: If the value is set too low, then you can run into issues where seemingly irrelevant commands are suggested. Likewise, if the value is set too high, then commands might not ever be suggested to the user. For both of the minimum similarities, the value should be values between 0 and 1 (inclusive), and should rarely go below 0.4.

### Analytics Configuration
#### Database Configuration
These are generic, non-specific database configuration options
- **database_enable** - Boolean - Indicate that you want the bot to upload analytics to the remote database.
- **database_detailed_table_name** - String - The name of the table to insert detailed, temporary data into.
- **database_anonymous_table_name** - String - The name of the table to insert anonymized, long term data into.
- **database_detailed_table_ttl_seconds** - Integer - The number of seconds before a record in the detailed table should be automatically removed.

#### DynamoDB Configuration
- **dynamo_db_credentials_file_path** - String - Path to your AWS credentials file, if it's not being picked up automatically. If empty, this will be ignored.
- **dynamo_db_resource** - String - The AWS boto-friendly resource to upload to.
- **dynamo_db_region_name** - String - The AWS region of your chosen `dynamo_db_resource`.
- **dynamo_db_primary_key** - String - The primary key of the above tables.
