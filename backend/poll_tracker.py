#!/usr/bin/env python3
"""
poll_tracker.py
────────────────
Polls Google Find My Network for 'BLE Smart Tracker' location every run.
Decrypts E2EE location data and appends it to tracking_log.csv
so the Leaflet JS Web App can visualize the path.
"""

import sys
import os
import time
import hashlib
import logging
import csv
import json

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_DIR = '/opt/tracking_poc/GoogleFindMyTools' # Update this path to where your auth repo is located
CSV_FILE = '../frontend/tracking_log.csv'

# Target device name (as shown in the Google Find My Device app)
# If you named it something else, change it here!
TARGET_DEVICE_NAME = 'MyTargetTracker'

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

# ── Setup path to GoogleFindMyTools ────────────────────────────────────────────
sys.path.insert(0, REPO_DIR)
os.chdir(REPO_DIR)

# ── GoogleFindMyTools imports ──────────────────────────────────────────────────
from NovaApi.ListDevices.nbe_list_devices import request_device_list
from NovaApi.ExecuteAction.LocateTracker.location_request import (
    create_location_request, generate_random_uuid
)
from NovaApi.nova_request import nova_request
from NovaApi.scopes import NOVA_ACTION_API_SCOPE
from Auth.fcm_receiver import FcmReceiver
from ProtoDecoders.decoder import parse_device_list_protobuf, get_canonic_ids, parse_device_update_protobuf
from ProtoDecoders import DeviceUpdate_pb2
from ProtoDecoders import Common_pb2
from NovaApi.ExecuteAction.LocateTracker.decrypt_locations import retrieve_identity_key, is_mcu_tracker
from FMDNCrypto.foreign_tracker_cryptor import decrypt as fmdn_decrypt
from KeyBackup.cloud_key_decryptor import decrypt_aes_gcm
from SpotApi.UploadPrecomputedPublicKeyIds.upload_precomputed_public_key_ids import refresh_custom_trackers


def get_device_canonic_id():
    """Get the canonic ID for the target device."""
    logging.info("Fetching device list from Google...")
    result_hex = request_device_list()
    device_list = parse_device_list_protobuf(result_hex)

    # Refresh custom tracker keys (safe no-op for non-MCU trackers)
    refresh_custom_trackers(device_list)

    canonic_ids = get_canonic_ids(device_list)

    for device_name, canonic_id in canonic_ids:
        logging.info(f"  Found device: {device_name} -> {canonic_id}")
        if TARGET_DEVICE_NAME.lower() in device_name.lower():
            return canonic_id, device_list

    logging.error(f"Target device '{TARGET_DEVICE_NAME}' not found!")
    return None, None


def fetch_location(canonic_id):
    """
    Send a location request for the device and wait for the FCM response.
    Returns the raw device_update protobuf.
    """
    logging.info(f"Requesting location for {canonic_id}...")
    result = None
    request_uuid = generate_random_uuid()

    def handle_response(response):
        nonlocal result
        device_update = parse_device_update_protobuf(response)
        if device_update.fcmMetadata.requestUuid == request_uuid:
            logging.info("Location response received via FCM.")
            result = parse_device_update_protobuf(response)

    fcm_token = FcmReceiver().register_for_location_updates(handle_response)
    hex_payload = create_location_request(canonic_id, fcm_token, request_uuid)
    nova_request(NOVA_ACTION_API_SCOPE, hex_payload)

    # Wait for response (timeout after 60 seconds)
    timeout = 60
    start = time.time()
    while result is None:
        if time.time() - start > timeout:
            logging.error("Timeout waiting for FCM location response!")
            return None
        time.sleep(0.2)

    return result


def decrypt_locations(device_update_protobuf):
    """Decrypt E2EE location data from the device update."""
    device_registration = device_update_protobuf.deviceMetadata.information.deviceRegistration
    identity_key = retrieve_identity_key(device_registration)
    locations_proto = device_update_protobuf.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    is_mcu = is_mcu_tracker(device_registration)

    recent_location = locations_proto.recentLocation
    recent_location_time = locations_proto.recentLocationTimestamp

    network_locations = list(locations_proto.networkLocations)
    network_locations_time = list(locations_proto.networkLocationTimestamps)

    if locations_proto.HasField("recentLocation"):
        network_locations.append(recent_location)
        network_locations_time.append(recent_location_time)

    decoded_locations = []
    for loc, loc_time in zip(network_locations, network_locations_time):
        try:
            if loc.status == Common_pb2.Status.SEMANTIC:
                continue
                
            encrypted_location = loc.geoLocation.encryptedReport.encryptedLocation
            public_key_random = loc.geoLocation.encryptedReport.publicKeyRandom

            if public_key_random == b"":  # Own Report
                identity_key_hash = hashlib.sha256(identity_key).digest()
                decrypted_location = decrypt_aes_gcm(identity_key_hash, encrypted_location)
            else:
                time_offset = 0 if is_mcu else loc.geoLocation.deviceTimeOffset
                decrypted_location = fmdn_decrypt(identity_key, encrypted_location, public_key_random, time_offset)

            proto_loc = DeviceUpdate_pb2.Location()
            proto_loc.ParseFromString(decrypted_location)

            decoded_locations.append({
                'type': 'geo',
                'latitude': proto_loc.latitude / 1e7,
                'longitude': proto_loc.longitude / 1e7,
                'accuracy': loc.geoLocation.accuracy,
                'timestamp': int(loc_time.seconds),
                'is_own_report': str(loc.geoLocation.encryptedReport.isOwnReport).lower()
            })
        except Exception as e:
            logging.warning(f"Failed to decrypt one location report: {e}")

    return decoded_locations


STATUS_FILE = '../frontend/status.json'

def write_to_csv(locations):
    """Write the most recent location to tracking_log.csv and status.json"""
    if not locations:
        logging.warning("No geo locations found.")
        return

    # Sort by timestamp descending → most recent first
    locations.sort(key=lambda x: x['timestamp'], reverse=True)
    latest = locations[0]
    
    # Write to status.json
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump({'last_seen': latest['timestamp']}, f)
    except Exception as e:
        logging.warning(f"Could not write status.json: {e}")

    # Check last line in CSV for deduplication
    last_lat = None
    last_lon = None
    file_exists = os.path.isfile(CSV_FILE)
    
    if file_exists:
        try:
            with open(CSV_FILE, 'r') as f:
                lines = f.readlines()
                if len(lines) > 1:
                    last_line = lines[-1].strip().split(',')
                    if len(last_line) >= 3:
                        last_lat = float(last_line[1])
                        last_lon = float(last_line[2])
        except Exception as e:
            logging.warning(f"Could not read last line of CSV for deduplication: {e}")

    current_lat = latest['latitude']
    current_lon = latest['longitude']

    # Tolerance for float comparison
    if last_lat is not None and last_lon is not None and abs(last_lat - current_lat) < 1e-6 and abs(last_lon - current_lon) < 1e-6:
        logging.info(f"Location unchanged ({current_lat}, {current_lon}). Updated status.json but skipped CSV append.")
        return
        
    with open(CSV_FILE, mode='a', newline='') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(['timestamp', 'latitude', 'longitude', 'accuracy', 'is_own_report', 'type'])
            
        writer.writerow([
            latest['timestamp'],
            latest['latitude'],
            latest['longitude'],
            latest.get('accuracy', -1),
            latest.get('is_own_report', 'false'),
            'geo'
        ])

    logging.info(f"Appended to CSV: {latest['latitude']:.6f}, {latest['longitude']:.6f} (time: {latest['timestamp']})")


def main():
    logging.info("=" * 50)
    logging.info("Starting BLE Smart Tracker location poll...")

    # Step 1: Get device list and find our tracker
    canonic_id, device_list = get_device_canonic_id()
    if canonic_id is None:
        logging.error("Aborting: target device not found.")
        sys.exit(1)

    # Step 2: Request location data
    device_update = fetch_location(canonic_id)
    if device_update is None:
        logging.error("Aborting: no location response received.")
        sys.exit(1)

    # Step 3: Decrypt locations
    locations = decrypt_locations(device_update)
    logging.info(f"Decrypted {len(locations)} location report(s).")

    # Step 4: Write to CSV
    write_to_csv(locations)

    # Stop FCM listener
    try:
        FcmReceiver().stop_listening()
    except Exception:
        pass


if __name__ == '__main__':
    main()
