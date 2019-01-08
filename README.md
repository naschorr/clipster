<p align="center"><img src="https://raw.githubusercontent.com/naschorr/clipster/master/resources/clipster-logo.png" width="150"/></p>

## Clipster
Soundboard bot for Discord

## Activate it on your server!
- Go to [this page](https://discordapp.com/oauth2/authorize?client_id=475695802350829568&scope=bot&permissions=104233024) on Discord's site.
- Select the server that you want Clipster to be added to.
- Hit the "Authorize" button.
- Start playing clips! (_Hint:_ join a voice channel and type in `=help`. You should check out the [**Commands**](https://github.com/naschorr/clipster#commands) section below, too!)

## Basic Commands
These commands allow for the basic operation of the bot, by anyone. Just type them into a public text channel while connected to a public voice channel. (Clipster can also read/join channels that you've given the permissions to)
- Clipster is designed to play premade clips, which you can see via the `=help` interface. Give that a try first.
- `=find [text]` - The bot will search its preloaded clips for the one whose contents most closely matches [text], and will display that command's name.
- `=random` - Plays a random clip from the list of preloaded clips.
- `=skip` - Skip a clip that you've requested, or start a vote to skip on someone else's clip.
- `=summon` - Summons the bot to join your voice channel.
- `=help` - Show the help screen.

## Hosting it yourself
- Make sure you've got [Python 3.6](https://www.python.org/downloads/) installed, and support for virtual environments (This assumes that you're on Python 3.6 with `venv` support, but older versions with `virtualenv` and `pyvenv` should also work.)
- `cd` into the directory that you'd like the project to go (If you're on Linux, I'd recommend '/usr/local/bin')
- `git clone https://github.com/naschorr/clipster`
- `python3 -m venv clipster/`
    + You may need to run: `apt install python3-venv` to enable virtual environments for Python 3 on Linux
- Activate your newly created venv
- `pip install -r requirements.txt`
    + If you run into issues during PyNaCl's installation, you may need to run: `apt install build-essential libffi-dev python3.5-dev` to install some supplemental features for the setup process.
- Make sure the [FFmpeg executable](https://www.ffmpeg.org/download.html) is in your system's `PATH` variable
- Create a [Discord app](https://discordapp.com/developers/applications/me), flag it as a bot, and put the bot token inside `clipster/token.json`
- Register the Bot with your server. Go to: `https://discordapp.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot&permissions=104233024`, but make sure to replace CLIENT_ID with your bot's client id.
- Select your server, and hit "Authorize"
- Check out `config.json` for any configuration you might want to do. It's set up to work well out of the box, but you may want to add admins, change pathing, or modify the number of votes required for a skip.

#### Windows Installation
- Nothing else to do! Everything should work just fine.

#### Linux Installation
Clipster as a Service (CaaS)
> *Note:* This assumes that your system uses systemd. You can check that by running `pidof systemd && echo "systemd" || echo "other"` in the terminal. If your system is using sysvinit, then you can just as easily build a cron job to handle running `clipster.py` on reboot. Just make sure to use your virtual environment's Python executable, and not the system's one.

- Assuming that your installation is in '/usr/local/bin/clipster', you'll want to move the `clipster.service` file into the systemd services folder with `mv clipster.service /etc/systemd/system/`
    + If your clipster installation is located elsewhere, just update the paths (`ExecStart` and `WorkingDirectory`) inside the `clipster.service` to point to your installation.
- Get the service working with `sudo systemctl daemon-reload && systemctl enable clipster && systemctl start clipster --no-block`
- Now you can control the Clipster service just like any other. For example, to restart: `sudo service clipster restart`

## Running your Clipster installation
- `cd` into the project's root
- Activate the venv (`source bin/activate` on Linux, `.\Scripts\activate` on Windows)
- Run `python code/clipster.py` to start Clipster

## Admin Commands
Admin commands allow for some users to have a little more control over the bot. For these to work, the `admin` array in `config.json` needs to have the desired usernames added to it. Usernames should be in the `Username#1234` format that Discord uses.
- `=admin skip` - Skip whatever's being spoken at the moment, regardless of who requested it.
- `=admin reload_clips` - Unloads, and then reloads the preset clips (found in the manifest files in each of the folders inside the 'clips' directory). This is handy for quickly adding new presets on the fly.
- `=admin reload_cogs` - Unloads, and then reloads the cogs registered to the bot (see clipster.py's ModuleManager class). Useful for debugging.
- `=admin disconnect` - Forces the bot to stop speaking, and disconnect from its current channel in the invoker's server.
- `=help admin` - Show the help screen for the admin commands.


## Configuration `config.json`

#### Discord Configuration
- **version** - String - The bot's current semantic version.
- **admins** - Array - Array of Discord usernames who have access to `\admin` commands. Uses `Username#1234` format.
- **activation_string** - String - The string that'll activate the Discord bot from chat messages.
- **description** - String - The bot's description. This is seen in the help interface.
- **channel_timeout** - Int - The time in seconds before the bot will leave its current voice channel due to inactivity.
- **channel_timeout_clip_paths** - Array - Array of paths to clips that the bot can speak right before it leaves the channel after it times out from inactivity. One clip is chosen randomly from the array.
- **skip_votes** - Int - The minimum number of votes needed by a channel to skip the currently playing speech.
- **skip_percentage** - Int - The minimum percentage of other users who need to request a skip before the currently playing speech will be skipped.

#### Bot Configuration
- **debug_level** - Int - The maximum threshold for printing debug statements to the terminal. Debug statements with a level of `0` are the most important, while statements with a level of `4` are the least important. See `debug_print()` in `utilities.py`.
- **token_file_path** - String - Force the bot to use a specific token, rather than the normal `token.json` file.
- **clips_folder_path** - String - Force the bot to use a specific clips folder, rather than the normal `clips/` folder.
- **ffmpeg_parameters** - String - Options to send to the FFmpeg executable before the `-i` flag.
- **ffmpeg_post_parameters** - String - Options to send to the FFmpeg executable after the `-i` flag.
- **string_similarity_algorithm** - String - The name of the algorithm to use when calculating how similar two given strings are. Currently only supports 'difflib'.
- **invalid_command_minimum_similarity** - Float - The minimum similarity an invalid command must have with an existing command before the existing command will be suggested as an alternative.
- **find_command_minimum_similarity** - Float - The minimum similarity the find command must have with an existing command, before the existing command will be suggested for use.
> *A quick note about minimum similarity*: If the value is set too low, then you can run into issues where seemingly irrelevant commands are suggested. Likewise, if the value is set too high, then commands might not ever be suggested to the user. For both of the minimum similarities, the value should be values between 0 and 1 (inclusive), and should rarely go below 0.4.

#### Analytics Configuration
- **boto_enable** - Boolean - Indicate that you want the bot to upload analytics to an Amazon AWS resource.
- **boto_resource** - String - The AWS boto-friendly resource to upload to. (I've only tried DynamoDB, but I'm fairly sure AWS' other storage resources would work if you wanted to tweak the code).
- **boto_region_name** - String - The AWS region of your chosen boto_resource.
- **boto_table_name** - String - The name of the table to insert into.
- **boto_primary_key** - String - The primary key of your chosen table.

## Lastly...
You should also take a look at my dedicated [clipster-clips repository](https://github.com/naschorr/clipster-clips). It's got a bunch of phrase files that can easily be put into your clips folder for even more customization.

Tested on Windows 10, and Ubuntu 16.04.