# Installing Clipster

## Basic Installation

- Make sure you've got [Python 3.10](https://www.python.org/downloads/) installed, and support for virtual environments (This assumes that you're on Python 3.10 with `venv` support)
- Double check that you're installing int a clean directory. If there's an old version of Clipster or an old venv then this likely won't work!
- `cd` into the directory that you'd like the project to go (If you're on Linux, I'd recommend '/usr/local/bin')
- Clone the repository to your machine: `git clone https://github.com/naschorr/clipster`
- Create a Python virtual environment inside the cloned repository: `python3.10 -m venv clipster/`
  - You may need to run: `apt install python3.10-venv` to enable virtual environments for Python 3.10 on Linux
- `cd clipster/`
- Activate your newly created venv (Run `source bin/activate` on Linux, or `.\Scripts\activate` on Windows)
- Install dependencies with: `pip install -r requirements.txt` (If you run into any issues with this, try running `pip install -r minimal-requirements.txt`. This installs the bare minimum number of packages and thus forces dependencies to resolve their dependencies automatically.)
  - If you run into issues during `PyNaCl`'s installation on Linux, you may need to run: `apt install build-essential libffi-dev python3.10-dev` to install some supplemental features for the setup process.
  - If you run into issue during `cffi`'s installation on Windows, you may need to install the Microsoft C++ Build Tools. You can find them [here](https://visualstudio.microsoft.com/visual-cpp-build-tools/).
- Make sure the [FFmpeg executable](https://www.ffmpeg.org/download.html) is in your system's `PATH` variable
- Create a [Discord app](https://discordapp.com/developers/applications/me), flag it as a bot, and put the bot token inside `config.json`, next to the `discord_token` key.
- Register the Bot with your server. Go to: `https://discordapp.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot&permissions=53803072`, but make sure to replace CLIENT_ID with your bot's client id.
- Select your server, and hit "Authorize"
- Check out `config.json` for any configuration you might want to do. It's set up to work well out of the box, change pathing, or modify the number of votes required for a skip. Note that linux based installations will require some extra tweaks to run Clipster, so check out the rest of this guide.

### Headless Installation

- Clipster as a Service (HaaS)
  > *Note:* This assumes that your system uses systemd. You can check that by running `pidof systemd && echo "systemd" || echo "other"` in the terminal. If your system is using sysvinit, then you can just as easily build a cron job to handle running `clipster.py` on reboot. Just make sure to use your virtual environment's Python executable, and not the system's one.

  - Assuming that your installation is in '/usr/local/bin/clipster', you'll want to move the `clipster.service` file into the systemd services folder with `mv clipster.service /etc/systemd/system/`
    - If your clipster installation is located elsewhere, just update the paths (`ExecStart` and `WorkingDirectory`) inside the `clipster.service` to point to your installation.
  - Get the service working with `sudo systemctl daemon-reload && systemctl enable clipster && systemctl start clipster --no-block`
  - Now you can control the Clipster service just like any other. For example, to restart: `sudo service clipster restart`

## Manually Running Clipster

Don't want to use services? You can manually invoke Python to start up Clipster as well.

- `cd` into the project's root
- Activate the virtual environment (Run `source bin/activate` on Linux, or `.\Scripts\activate` on Windows)
- `cd` into `clipster/code/`
- Run `python clipster.py` to start Clipster
