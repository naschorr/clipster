# Admin Commands
Admin commands allow for the bot owner to have convenient access to the bot from within Discord.

- `@Clipster admin sync_local` - Syncs the bot's slash commands to the user's current guild.
- `@Clipster admin sync_global` - Syncs the bot's slash commands to all guilds.
- `@Clipster admin clear_local` - Removes the bot's slash commands from the user'scurrent guild.
- `@Clipster admin skip` - Skip whatever's being spoken at the moment, regardless of who requested it.
- `@Clipster admin reload_clips` - Unloads, and then reloads the clips. This is handy for quickly adding new clips on the fly.
- `@Clipster admin reload_cogs` - Unloads, and then reloads the cogs registered to the bot. Useful for debugging.
- `@Clipster admin disconnect` - Forces the bot to stop speaking, and disconnect from its current channel in the invoker's server.
