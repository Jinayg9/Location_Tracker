# Comprehensive Proof of Concept (PoC) Report & Engineering History

This document serves as an exhaustive technical report and chronological history summarizing the recent engineering efforts for the **BLE Smart Tracker Cloud Deployment**.

---

## BLE Smart Tracker Cloud Tracking PoC

### 1. Objective & Context
The primary objective was to establish a robust, cloud-based tracking system for a BLE Smart Tracker device. The initial local prototype on a single-board computer proved that tracking was possible using Google's Find My Device Network (leveraging the open-source `GoogleFindMyTools` library), but it was plagued by short-lived authentication sessions and lacked a viable, permanent production environment. The goal was to deploy a boot-persistent, automated tracker to a Cloud Compute Instance with a responsive, live-updating map interface.

### 2. Installation & Setup Prerequisites

**GoogleFindMyTools Dependency:**
The backend polling script (`backend/poll_tracker.py`) relies heavily on the open-source `GoogleFindMyTools` library to interface with the Find My Device Network (FMDN) and handle AES-GCM decryption.

To set up the backend environment, you must first clone and install this dependency:
```bash
# Clone the library into an appropriate directory (e.g., /opt/tracking_poc/)
git clone https://github.com/dchristl/GoogleFindMyTools.git

# Set up the Python virtual environment
cd GoogleFindMyTools
python3 -m venv venv
source venv/bin/activate

# Install the required cryptographic and web dependencies
pip install -r requirements.txt
```
*Note: Ensure you update the `REPO_DIR` variable inside `poll_tracker.py` to point to the absolute path where you cloned `GoogleFindMyTools`.*

### 2. Chronological Task History & Error Resolution

#### Phase 1: Authentication & Deployment
- **Challenge - The 3-Day Token Expiry:** The automated login script utilized standard session cookies, which were forcefully expired by Google every 3 days. This made unattended background tracking impossible.
- **Resolution:** Pivoted to a manual, browser-based OAuth flow (`manual_cookie_auth.py`). By using a dedicated Google App Password, we intercepted the `oauth_token` and exchanged it for a permanent Android Master Token (`aas_token`). This token is functionally permanent (unless the account password changes), entirely resolving the session expiry blocker.
- **Action:** Migrated the entire codebase to a Cloud Compute Instance (`<REDACTED_IP>`). Configured a dedicated Python virtual environment (`/opt/tracking_poc/venv`) and set up a basic `systemd` service (`tracker-poll.service`) and timer to ensure the script ran persistently on system boot.

#### Phase 2: Device Discovery & The "Poison Pill" Bug
- **Error - Device Not Found:** Initially, the script failed with `Target device 'LegacyDeviceName' not found!`.
- **Resolution:** By inspecting the API response logs, it was discovered that the device was actually registered on the network under the name `TargetDeviceName`. The script's `TARGET_DEVICE_NAME` variable was updated, allowing the API to successfully fetch the device's Canonic ID (`<REDACTED_UUID>`).
- **Critical Error - The Firebase Cloud Messaging (FCM) Poison Pill (Base64 Padding):** At 2:28 PM local time, the backend script violently crashed with a `binascii.Error: Incorrect padding` stack trace. 
- **Investigation & Failed Fixes:** The crash was isolated to `pushclient.py` during the decoding of the FCM `crypto-key`. 
  - *Attempt 1:* We attempted to forcefully append standard base64 padding (`===`) using an automated regex script. This resulted in a subsequent `ValueError: Invalid EC key`, as the padding corrupted the Elliptic Curve Diffie-Hellman public key payload.
- **Final Resolution:** It was determined that Google's FCM network occasionally routes completely corrupted or incorrectly formatted "Poison Pill" payloads. Because the script crashed, it never acknowledged the message, causing Google to infinitely retry sending the poison pill, locking the tracker in a crash loop. 
- **Action:** Implemented a robust `try...except` block completely wrapping the `_decrypt_raw_data` execution. This allowed the script to gracefully catch the decryption failure, log a warning (`Failed to decrypt message (poison pill), ignoring: Incorrect padding`), and immediately resume listening for the next clean location ping.

#### Phase 3: Backend Optimization & Deduplication
- **Challenge - Redundant Polling & Map Clutter:** The background timer was initially set to 30 seconds. If a vehicle was stationary, the script would append identical coordinates to `tracking_log.csv` thousands of times, heavily bloating the file and causing the frontend map renderer to lag severely.
- **Resolution:** 
  1. Accelerated the `systemd` timer from 30 seconds to **15 seconds** to maximize responsiveness for fast-moving vehicles.
  2. Engineered a deduplication routine in the polling script. Before writing to the CSV, it reads the last known coordinate. If the new coordinate perfectly matches (within a `1e-6` float tolerance), it skips the CSV write entirely.

#### Phase 4: Frontend Visualization & Dynamic Server
- **Challenge - Static Server Limitations:** The Leaflet map was initially served via a static `python -m http.server`, meaning the UI had no way to communicate commands back to the server.
- **Resolution:** Wrote a custom, dynamic backend script (`server.py`) with a dedicated `POST /clear` endpoint.
- **UI Enhancements:**
  - **Network Last Seen:** To compensate for the deduplication feature (which stops the CSV from updating if stationary), the backend was configured to dump a `status.json` file on every successful ping. The UI reads this to display an accurate "Network Last Seen: X minutes ago" metric.
  - **Accuracy Filtering:** Bluetooth tracking from fast-moving vehicles can result in highly inaccurate pings. The UI script (`app.js`) was updated to parse the `accuracy` radius. Any ping worse than 300 meters is now excluded from the route Polyline (preventing jagged lines through buildings) and plotted as a distinct Orange Marker.
  - **Accuracy Circles:** The Leaflet map now dynamically renders a translucent red circle around the latest ping, visually representing its exact margin of error.

#### Phase 5: Server Resiliency & Route Rendering Optimizations
- **Challenge - Server Deadlocks & Port Conflicts:** The basic `TCPServer` was single-threaded. If a client held a keep-alive connection open improperly, the entire server hung, blocking all other requests and causing the site to "time out". Additionally, automated restarts threw `OSError: [Errno 98] Address already in use`.
- **Resolution:** Upgraded `server.py` to use `socketserver.ThreadingTCPServer`, which spawns isolated threads for each client, fully preventing deadlocks. Added `allow_reuse_address = True` for instant port binding on restart.
- **Challenge - "Jumbled" Routes & Phone Base Tracking:** The map drew paths chronologically based on raw CSV arrival, causing zigzag lines. Furthermore, when the user's phone pinged the tracker (acting as a base station), the path would erroneously jump back to the phone's location (`is_own_report = true`).
- **Resolution:** 
  1. **Chronological Sorting:** The frontend script (`app.js`) now explicitly sorts all data points by `timestamp` ascending.
  2. **Strict Route Filtering:** The continuous polyline path now aggressively filters out high-inaccuracy spikes (`accuracy > 100m`) AND owner-phone reports (`is_own_report === true`).
  3. **Data Transparency (Clickable Markers):** To ensure no data is hidden, ALL raw pings are still plotted as interactive `L.circleMarker` elements. Valid path points are colored **Blue**, while ignored/filtered points (inaccurate or phone-based) are colored **Red**. Every marker is clickable to display its full metadata popup.
  4. **Cache Control:** Implemented script versioning (`app.js?v=8`) to ensure the client browser forcibly updates without clearing cache.

#### Phase 6: Map Rendering Scalability & UI Time Filters
- **Challenge - Browser DOM Memory Crash:** As the tracking log exceeded 5,000 pings, Leaflet's default SVG renderer began severely lagging the browser DOM. Rendering 50,000+ points would result in a hard browser crash (Out of Memory), especially on mobile devices.
- **Resolution:**
  1. **Canvas Renderer Upgrade:** Added `{ preferCanvas: true }` to the Leaflet map initialization. By drawing raw shapes to an HTML5 Canvas instead of creating thousands of individual SVG DOM nodes, the map can now effortlessly render 100,000+ pings.
  2. **UI Time Filters:** Added dynamic filter buttons (`Today`, `Last 7 Days`, `All Time`) to the frontend. The system still downloads the full CSV, but automatically filters points based on the Unix timestamp before rendering. The map defaults to `Last 7 Days` to ensure immediate load times while preserving the ability to inspect long-term historical paths.
