# AERIS Ground Station — Wi-Fi Hotspot Setup

This guide configures the Raspberry Pi 4B Ground Station as a standalone
off-grid Wi-Fi access point so a laptop can reach the dashboard at
http://192.168.4.1 without Ethernet or internet.

## Prerequisites
- Raspberry Pi 4B running Raspberry Pi OS (64-bit, Bookworm or later)
- SSH access to the Pi (initially via Ethernet or existing Wi-Fi)
- Onboard Wi-Fi interface (wlan0)

## Step 1: Inspect current network state
```bash
ip link show
nmcli device status
rfkill list
```
Confirm wlan0 exists and is not blocked. Note your SSH connection interface.

## Step 2: Create the hotspot with NetworkManager (recommended on Bookworm)
```bash
sudo nmcli con add type wifi ifname wlan0 con-name aeris-hotspot autoconnect yes \
  wifi.mode ap wifi.ssid "AERIS-GroundStation" wifi.band bg wifi.channel 6 \
  ipv4.method shared ipv4.addresses 192.168.4.1/24

sudo nmcli con modify aeris-hotspot wifi-sec.key-mgmt wpa-psk
sudo nmcli con modify aeris-hotspot wifi-sec.psk "aeris-secure-pass"
sudo nmcli con up aeris-hotspot
```

## Step 3: Verify
```bash
ip addr show wlan0          # should show 192.168.4.1
nmcli con show --active
```
From laptop: connect to "AERIS-GroundStation", then open http://192.168.4.1

## Step 4: mDNS (optional, for aeris.local)
```bash
sudo apt install -y avahi-daemon
sudo systemctl enable --now avahi-daemon
```

## Recovery / Rollback
```bash
sudo nmcli con down aeris-hotspot
sudo nmcli con delete aeris-hotspot
# Re-enable normal Wi-Fi client mode:
sudo nmcli radio wifi on
```

## Important Notes
- Do NOT run hostapd + NetworkManager simultaneously.
- If SSH is over Wi-Fi, configure hotspot first via Ethernet to avoid lockout.
- The hotspot auto-starts on boot via NetworkManager autoconnect.