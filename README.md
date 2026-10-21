# Philips TV Web Remote

A remote control for a Philips 58PUS8555/12 that you open in your phone's browser.

## How it works

1. A small Python server runs on your Mac.
2. Your phone opens a web page served by that server.
3. The server forwards each button press to the TV's built-in API
   (JointSpace v6, HTTPS on port 1926).

The phone cannot talk to the TV directly. The TV needs a secure connection
with a self-signed certificate and a password check, which browsers block.

## Run it

```bash
python3 server.py
```

Then on your Android phone, on the same Wi-Fi, open `http://<mac-ip>:8888`.
Find the Mac's IP with `ipconfig getifaddr en0`.

The first time macOS may ask whether Python can accept incoming connections.
Click Allow.

Tip: in Chrome on Android, use the menu and pick "Add to Home screen". It
then opens like a normal app.

## First-time pairing

1. Turn the TV on.
2. Open the page and tap "Show PIN on TV".
3. Type the 4-digit PIN from the TV screen and tap "Pair".

The key the TV gives back is saved in `config.json`. That file is private to
your user and is not tracked by git.

## Switching the TV on from standby

When the TV is fully off it leaves the network. The power button then sends a
Wake-on-LAN signal instead. For this to work, enable this on the TV:
Settings > Wireless and networks > Wired or Wi-Fi > Switch on with Wi-Fi (WoWLAN).

## Settings

Set these in `config.json` or as environment variables:

| Setting  | Default             | Meaning                 |
|----------|---------------------|-------------------------|
| `tv_ip`  | `192.168.1.22`      | TV address (`TV_IP`)    |
| `tv_mac` | `f8:4f:ad:a0:00:64` | TV MAC, for wake-up (`TV_MAC`) |
| `PORT`   | `8888`              | Web page port (env only) |

Give the TV a fixed IP in your router so the address does not change.

## Security note

Anyone on your home Wi-Fi can open the page and control the TV. There is no
login. Do not expose port 8888 to the internet.
