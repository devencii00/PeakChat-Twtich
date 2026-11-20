<p align="center">
  <img src="peakchat.png" alt="PeakChat logo" width="150">
</p>

<h1 align="center">PeakChat</h1>

<p align="center">
  A desktop tool for spotting high-energy moments in Twitch chat, live or from a VOD.
</p>

PeakChat monitors public Twitch chat and highlights sudden activity spikes against the stream's recent chat pace. Use it while a stream is live, or analyze chat from a VOD to find moments worth reviewing.

## Features

- **Live chat monitoring** with real-time message, speed, and average activity stats.
- **Hype spike detection** that compares each 10-second chat window with recent activity. Choose a sensitivity that fits the stream.
- **Multiple streamer tabs** with saved channels, spike cards, and manual markers.
- **VOD analysis** from a local ChatDownloader JSON file or a URL. Twitch VODs and clips, YouTube, and Kick URLs are supported through ChatDownloader.
- **Progress and cancellation** for URL imports.
- **CSV logs and exports** for chat and detected spikes.
- **Customizable UI** with light/dark themes, fullscreen, animated background, chat font, and spike-sound settings.

## Quick start (Windows)

1. Install Python 3 with Tcl/Tk support.
2. Download or clone this repository.
3. Double-click `launcher.pyw`, or run:

   ```powershell
   py launcher.pyw
   ```

The launcher creates a project-local `.venv`, checks the required packages, installs anything missing, and starts PeakChat. Python itself must already be installed; the launcher does not install Python. An internet connection is needed for first-run package installation.

## Manual setup

From the project directory, create a virtual environment and install the app's packages:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install customtkinter websockets requests Pillow "tkinter-icons[material]" pygame-ce chat-downloader
.\.venv\Scripts\python.exe .\twitch_peakchat.py
```

The launcher installs the same dependencies automatically. Tkinter is included with many Python distributions; install a Python build that includes Tcl/Tk if the launcher cannot open.

## Using PeakChat

1. Add a tab and enter a Twitch channel name.
2. Press **Start**. The app enables **Stop** after it connects to chat.
3. Adjust the sensitivity from the tab controls. Higher sensitivity focuses on larger bursts.
4. Use **Import VOD** to select a local ChatDownloader JSON file or provide a VOD URL.
5. Review spike cards and use the export controls to save results.

Live chat and spike CSV logs are written under `twitch_logs` by default. Change the log location in **Settings**.

### Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+T` | Open a new tab |
| `Ctrl+W` | Close the active tab |
| `Alt+Enter`, `Ctrl+Enter`, or `F11` | Toggle fullscreen |

## Requirements and local data

- Python 3 with Tcl/Tk
- `customtkinter`
- `websockets`
- `requests`
- `Pillow`
- `tkinter-icons[material]`
- `pygame-ce`
- `chat-downloader`

For live chat, PeakChat connects anonymously to public Twitch chat; a Twitch login is not required. Settings, saved streamers, avatar cache, and generated logs are stored locally in the project directory. Network access is used for chat, VOD retrieval, and first-run dependency installation.

The app icon is `tweakchat.ico`; `peakchat.png` is used as the app's logo. Keep these assets beside the Python files when running from source.

## License

This repository does not currently include a `LICENSE` file. Add one to specify reuse and redistribution terms.
