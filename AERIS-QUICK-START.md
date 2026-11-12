# AERIS Quick Start — Wi-Fi & Dashboard Guide

For your first time using AERIS. No technical knowledge needed.

---

## What you need

- The AERIS box (Raspberry Pi) with its **SD card already inserted**
- A **USB-C power supply** (5 V / 3 A)
- Your laptop with Wi-Fi
- A **web browser** (Chrome, Edge, Firefox — all fine)

---

## Step 1 — Turn it on

1. Plug the power supply into the AERIS box.
2. Wait about **30 seconds** while it boots.
3. That's it — **the Wi-Fi and the dashboard start by themselves.** You do not need to run any command.

> Want to see that it worked? On the AERIS box, open a terminal and type:
> ```
> curl http://127.0.0.1/health
> ```
> You should see `{"status":"ok","service":"aeris-backend"}`.

---

## Step 2 — Get the Wi-Fi password

The network name is **`AERIS-GroundStation`**.

To see its password, open a terminal **on the AERIS box** and run:

```
sudo nmcli --show-secrets -g 802-11-wireless-security.psk connection show aeris-hotspot
```

It prints one short word. **Write it down** — you need it in Step 3.

---

## Step 3 — Join the Wi-Fi on your laptop

1. On your laptop, click the **Wi-Fi icon** in the task bar / menu bar.
2. Look for the network named **`AERIS-GroundStation`**.
3. Select it and enter the password from Step 2.
4. Choose **"Connect"**.

> **Tip:** AERIS broadcasts on the normal **2.4 GHz** band (the one your router also uses). If your laptop has both a 2.4 GHz and a 5 GHz option, pick the 2.4 GHz one.

Your laptop should now say it is **connected** to `AERIS-GroundStation`.

---

## Step 4 — Open the dashboard

Open your web browser and go to:

```
http://192.168.4.1/
```

You should see a page titled **AERIS**.

**If that doesn't work, try this instead:**
```
http://raspberrypi.local/
```

> **Note:** older notes mention `aeris.local` — that address does **not** work on this machine. Use `192.168.4.1` or `raspberrypi.local`.

---

## Step 5 — Sign in

The first time you open it, AERIS asks for an **access token**.

1. On the AERIS box, in a terminal, run:
   ```
   sudo cat ~/aeris-project/ground-station/backend/.aeris_auth_token
   ```
2. Copy the long word it prints.
3. Paste it into the **Access token** box on the dashboard and press **Connect**.

The dashboard then opens and starts showing live data.

> You only do this once per browser — the token is remembered.

---

## If something doesn't work

| Problem | What to do |
|---|---|
| **`AERIS-GroundStation` doesn't appear in your Wi-Fi list** | On the AERIS box: `sudo rfkill unblock wifi` then `sudo nmcli radio wifi on` |
| **Your laptop connects but you get no internet** | **That's normal.** AERIS is a closed network — it deliberately has no internet access. You can still reach the dashboard. |
| **The browser says "can't reach the page"** | Make sure you are on `http://192.168.4.1/` (include `http://` and the trailing `/`). Then try `http://raspberrypi.local/`. |
| **Still no page?** | On the AERIS box run `sudo nmcli con up aeris-hotspot` and wait 10 seconds, then reload the page. |
| **The dashboard asks for the token again** | The token was not saved — repeat Step 5. If you open a **different browser** (e.g. Chrome instead of Firefox), you must enter it again. |
| **The dashboard loads but shows things as DISCONNECTED** | The hardware status is real. Check `sudo systemctl status aeris-backend` on the AERIS box. If the thermal camera or LoRa radio really is unplugged, the dashboard is correctly telling you so. |
| **Nothing works and you want a clean restart** | On the AERIS box: `sudo ./stop_aeris.sh` then `sudo ./start_aeris.sh` |

---

## Useful commands (run these on the AERIS box)

| What you want | Command |
|---|---|
| Start everything (fast, no rebuild) | `sudo ./start_aeris.sh` |
| Full repair / rebuild (needs internet) | `sudo ./aeris.sh` |
| Stop everything | `sudo ./stop_aeris.sh` |
| Is the dashboard server alive? | `curl http://127.0.0.1/health` |
| Is the Wi-Fi hotspot up? | `nmcli device status` |
| Turn the hotspot back on | `sudo nmcli con up aeris-hotspot` |
| See what the system is doing | `sudo systemctl status aeris-backend` |
| Follow the live log | `sudo journalctl -u aeris-backend -f` |

---

## One-minute summary

```
1. Plug in power, wait 30 seconds            -> it starts by itself
2. sudo nmcli --show-secrets -g 802-11-wireless-security.psk connection show aeris-hotspot
3. Join the Wi-Fi "AERIS-GroundStation"     -> use the password from step 2
4. Open http://192.168.4.1/                 -> the dashboard
5. sudo cat ~/aeris-project/ground-station/backend/.aeris_auth_token
   paste that into the Access token box     -> you're in
```
