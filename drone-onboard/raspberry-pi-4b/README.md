# AERIS - Raspberry Pi Detection Module

Detection module para sa AERIS.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      Detection Pipeline                          │
└─────────────────────────────────────────────────────────────────┘

Camera (USB/IP) ──▶ OpenCV ──▶ YOLOv8 ──▶ JSON ──▶ WebSocket ──▶ Dashboard
                                    │
                                    ▼
                            ┌───────────────┐
                            │  Detection     │
                            │  Screenshot    │
                            │  (saved)       │
                            └───────────────┘
```

---

## Hardware Requirements

| Component | Specification |
|-----------|---------------|
| Raspberry Pi | 4B (4GB) or 5 (8GB) |
| Camera | USB webcam or Raspberry Pi Camera |
| Storage | 32GB+ SD card |
| Power | 5V 3A USB-C |
| LoRa Module | SX1278/RFM95W (optional) |

---

## Software Requirements

### 1. Raspberry Pi OS (64-bit)

Download from: https://www.raspberrypi.com/software/operating-systems/

### 2. Python 3.9+

```bash
# Check Python version
python3 --version

# Install pip if missing
sudo apt install -y python3-pip

# Install virtual environment
python3 -m venv venv
source venv/bin/activate
```

### 3. Required Python Packages

```bash
# Activate virtual environment first
source venv/bin/activate

# Install PyTorch (CPU version)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Install OpenCV
pip install opencv-python-headless

# Install Ultralytics (YOLOv8)
pip install ultralytics

# Install other dependencies
pip install numpy pandas requests websocket-client
```

### 4. Screen (for background processes)

```bash
sudo apt install -y screen
```

---

## Quick Start

### 1. Flash OS to SD Card

Use Raspberry Pi Imager:
1. Choose OS → Raspberry Pi OS (64-bit)
2. Choose Storage → Your SD card
3. Click the gear icon:
   - Set hostname: `aeris-pi`
   - Enable SSH
   - Configure Wi-Fi
   - Set username/password
4. Write

### 2. Initial Setup

```bash
# SSH into Pi
ssh pi@aeris-pi.local
# Or use IP: ssh pi@192.168.1.x

# Update system
sudo apt update && sudo apt upgrade -y

# Enable camera (if using Pi Camera)
sudo raspi-config
# Navigate to: Interface Options > Camera > Enable

# Reboot
sudo reboot
```

### 3. Install Detection Software

```bash
# SSH back in
ssh pi@aeris-pi.local

# Clone project (or copy files via USB)
git clone https://github.com/eldrin-dotcom/AERIS.git
cd AERIS/raspberry-pi-4b  # or raspberry-pi-5

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Test YOLOv8
python3 -c "from ultralytics import YOLO; print('YOLOv8 OK')"
```

### 4. Run Detection

```bash
# Activate virtual environment
source venv/bin/activate

# Test with webcam
python main.py

# Press 'q' to quit
```

---

## Using Screen (Background Process)

> For an unattended device use the **systemd service** in
> [Production Deployment](#production-deployment) instead — it restarts the
> pipeline after a crash and starts it automatically on every boot. `screen`
> is only useful while you are debugging in the foreground.

### Start Detection in Background

```bash
# Create new screen
screen -S aeris

# Activate environment
source venv/bin/activate

# Run detection
python main.py

# Detach: Press Ctrl+A, then D
```

### Reconnect to Screen

```bash
# List screens
screen -ls

# Reconnect
screen -r aeris

# If attached elsewhere, force detach
screen -d -r aeris
```

### Kill Screen

```bash
screen -X -S aeris quit
```

---

## Configuration

Edit `config.py`:

```python
# Device Configuration
DEVICE_NAME = "AERIS RPi 4B"
DEVICE_VERSION = "1.0"
CPU_MODEL = "Broadcom BCM2711"
RAM_SIZE = "4GB"
STATUS = "Active"

# Camera Configuration
CAMERA_INDEX = 0  # 0 for USB webcam, or use IP camera URL

# IP Webcam (Android phone as camera)
IP_CAMERA_URL = "http://192.168.1.100:8080/video"

# Detection Settings
CONFIDENCE_THRESHOLD = 0.5

# Backend Configuration
BACKEND_WS_URL = "ws://192.168.1.x:8000/ws"  # GCS IP address
BACKEND_API_URL = "http://192.168.1.x:8000"  # GCS IP address
```

---

## Project Structure

```
raspberry-pi-4b/
├── main.py              # Main entry point
├── config.py            # Configuration
├── detector.py          # YOLOv8 detector
├── human_detection.py   # Detection wrapper
├── vital_signs.py       # Simulated vital signs
├── packet.py            # JSON packet builder
├── sender.py            # LoRa/WebSocket sender
├── receiver.py          # LoRa/WebSocket receiver
├── logger.py            # CSV/JSON logging
├── dashboard.py         # Flask dashboard (optional)
├── utils.py             # Utilities
├── test_cam.py          # Camera test
├── requirements.txt     # Python dependencies
└── README.md            # This file
```

---

## Testing

### Test Camera

```bash
# Quick camera test
python test_cam.py

# Should show preview window, press 'q' to quit
```

### Test Detection Only

```bash
# Run without sending to backend
# Edit main.py to comment out sender calls

# Run
python main.py
```

---

## Sending Data to Dashboard

### WebSocket Method (Recommended)

```python
# In sender.py
import websocket
import json

def send_detection(data):
    ws = websocket.WebSocket()
    ws.connect("ws://192.168.1.100:8000/ws")  # GCS IP
    ws.send(json.dumps(data))
    ws.close()

def send_telemetry(data):
    ws = websocket.WebSocket()
    ws.connect("ws://192.168.1.100:8000/ws")
    ws.send(json.dumps(data))
    ws.close()
```

### REST API Method

```python
import requests

def send_detection(data):
    response = requests.post(
        "http://192.168.1.100:8000/api/detection",
        json=data
    )
    return response.json()
```

---

## Network Setup

### Static IP on Raspberry Pi

```bash
# Edit dhcpcd.conf
sudo nano /etc/dhcpcd.conf

# Add at the bottom:
interface wlan0
    static ip_address=192.168.1.100/24
    static routers=192.168.1.1
    static domain_name_servers=192.168.1.1

# Restart networking
sudo systemctl restart dhcpcd
```

### Find GCS IP Address

On Ground Control Station:

```bash
# Windows
ipconfig

# Look for IPv4 Address under Wi-Fi adapter
# Example: 192.168.1.50
```

---

## Performance Tips

### For Raspberry Pi 4B

```bash
# Enable swap
sudo dphys-swapfile swapoff
sudo nano /etc/dphys-swapfile
# Set CONF_SWAPSIZE=1024
sudo dphys-swapfile setup
sudo dphys-swapfile swapon

# Use YOLOv8n (nano) for speed
# Or use YOLOv8s (small) for better accuracy
```

### For Raspberry Pi 5

```bash
# Pi 5 has better performance
# Can use YOLOv8m (medium) for better accuracy
```

---

## Troubleshooting

### Camera Not Detected

```bash
# Check USB
lsusb

# Check video devices
ls -la /dev/video*

# Test with VLC
cvlc v4l2:///dev/video0
```

### YOLOv8 Download Failed

```bash
# Manually download weights
wget https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt

# Place in project folder
mv yolov8n.pt ~/.ultralytics/
```

### WebSocket Connection Failed

```bash
# Test connection manually
pip install websocket-client

python -c "
import websocket
ws = websocket.create_connection('ws://192.168.1.100:8000/ws')
ws.send('test')
print(ws.recv())
ws.close()
"

# Check firewall
sudo ufw status
sudo ufw allow 8000
```

### Memory Issues

```bash
# Check memory
free -h

# Clear memory
sync && echo 3 | sudo tee /proc/sys/vm/drop_caches

# Monitor
htop
```

---

## Production Deployment

### Auto-start on Boot

The pipeline ships with a systemd unit, `aeris-onboard.service`, and an
installer that creates the virtualenv, verifies every dependency, installs the
unit and enables it for boot:

```bash
cd ~/aeris-project/drone-onboard/raspberry-pi-4b
sudo ./install_onboard_service.sh
```

What the installer does:

1. **Hardware conflict check** — refuses to run on a Pi that already runs the
   ground station. The ground station backend and the onboard pipeline drive
   the *same* sensors (MLX90640 on I2C1 @ `0x33`, SX1278 on SPI0 with GPIO23
   RESET and GPIO24 NSS). The ground station *receives* LoRa, the onboard
   pipeline *transmits* it — one radio cannot do both at once. Use `--force`
   only if the hardware really is separate.
2. **Virtualenv** — creates it if missing and verifies that every module the
   pipeline imports is importable (`numpy`, `flask`, `cv2`, `board`, `busio`,
   `adafruit_mlx90640`, `spidev`, `RPi.GPIO`, `SX127x`). Missing packages are
   installed individually, so a working environment is never downgraded.
3. **Import check** — `main.py` must import cleanly, otherwise the service
   would crash-loop on every boot.
4. **systemd unit** — installs `aeris-onboard.service` and enables it.

Verify:

```bash
sudo systemctl status aeris-onboard
sudo journalctl -u aeris-onboard -f
```

The local Flask panel is served on port `5000`.

The unit itself (`aeris-onboard.service`) uses:

```ini
[Unit]
After=network-online.target
Wants=network-online.target
Conflicts=aeris-backend.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=/home/piaeris/aeris-project/drone-onboard/raspberry-pi-4b
ExecStart=/home/piaeris/aeris-project/drone-onboard/raspberry-pi-4b/venv/bin/python -u main.py
Restart=always
RestartSec=10
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
```

`StartLimitIntervalSec=0` means systemd never gives up restarting the
pipeline; `Restart=always` means it comes back after a crash or a power cut.

> The full start-up architecture of the whole system — including the ground
> station — is documented in [BOOT_AUTOSTART.md](../../BOOT_AUTOSTART.md).

---

## Security

### Change Default Password

```bash
passwd
```

### Disable Password SSH

```bash
# Generate SSH key on GCS
ssh-keygen -t rsa
ssh-copy-id pi@aeris-pi.local

# Disable password authentication
sudo nano /etc/ssh/sshd_config
# Set: PasswordAuthentication no
sudo systemctl restart sshd
```

---

## Support

For issues, check:
1. Camera connection
2. Network connectivity (ping test)
3. Python dependencies installed
4. YOLOv8 model downloaded
5. WebSocket port not blocked
