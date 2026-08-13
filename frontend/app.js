// Initialize Leaflet Map
// Using a dark theme tile layer to match the glassmorphism UI
const map = L.map('map').setView([19.0760, 72.8777], 14);

L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 20
}).addTo(map);

// Custom icons for start and end points
const createIcon = (color) => L.divIcon({
    className: 'custom-icon',
    html: `<div style="background-color: ${color}; width: 16px; height: 16px; border-radius: 50%; border: 3px solid white; box-shadow: 0 0 10px ${color};"></div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11]
});

const startIcon = createIcon('#10b981'); // Green
const endIcon = createIcon('#ef4444');   // Red
const lowAccuracyIcon = createIcon('#f59e0b'); // Orange

// Clear Map Button Logic
document.getElementById('clear-map-btn')?.addEventListener('click', () => {
    if (confirm("Are you sure you want to clear all map data?")) {
        fetch('/clear', { method: 'POST' })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    location.reload();
                } else {
                    alert("Error clearing data: " + data.message);
                }
            })
            .catch(err => alert("Failed to clear map: " + err));
    }
});

// Fetch network status
fetch('status.json?v=' + new Date().getTime())
    .then(res => res.json())
    .then(data => {
        if (data.last_seen) {
            const time = new Date(data.last_seen * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            document.getElementById('network-last-seen').innerText = time;
        }
    })
    .catch(err => console.log("No status.json yet"));

// Load and parse the CSV
fetch('tracking_log.csv?v=' + new Date().getTime())
    .then(response => response.text())
    .then(csvText => {
        Papa.parse(csvText, {
            header: true,
            dynamicTyping: true,
            skipEmptyLines: true,
            complete: function(results) {
                const data = results.data;
                processTrackingData(data);
            }
        });
    })
    .catch(err => console.error("Error loading CSV:", err));

function processTrackingData(data) {
    if (!data || data.length === 0) return;

    // Filter valid geo points, sort chronologically
    let allPoints = data.filter(d => d.latitude && d.longitude);
    allPoints.sort((a, b) => a.timestamp - b.timestamp);
    
    // Update stats with ALL valid points
    document.getElementById('ping-count').innerText = allPoints.length;
    
    if (allPoints.length > 0) {
        const lastPoint = allPoints[allPoints.length - 1];
        const lastTime = new Date(lastPoint.timestamp * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        document.getElementById('last-updated').innerText = lastTime;
    }

    // Filter out points with very poor accuracy and 'is_own_report' for the PATH ONLY
    let pathPoints = allPoints.filter(d => (!d.accuracy || d.accuracy <= 100) && d.is_own_report !== true);

    // Extract coordinates for Polyline from pathPoints
    const latlngs = pathPoints.map(p => [p.latitude, p.longitude]);

    // Draw the path
    if (latlngs.length > 1) {
        const path = L.polyline(latlngs, {
            color: '#3b82f6', // Accent blue
            weight: 4,
            opacity: 0.8,
            smoothFactor: 1,
            interactive: false // Allow clicks to pass through to markers
        }).addTo(map);

        // Fit map to show the whole path
        map.fitBounds(path.getBounds(), { padding: [50, 50] });
    } else if (latlngs.length > 0) {
        map.setView(latlngs[0], 15);
    } else if (allPoints.length > 0) {
        map.setView([allPoints[allPoints.length - 1].latitude, allPoints[allPoints.length - 1].longitude], 15);
    }

    // Add Markers for ALL points
    allPoints.forEach((point, index) => {
        const time = new Date(point.timestamp * 1000).toLocaleString();
        const popupContent = `
            <span class="popup-time">${time}</span>
            <span class="popup-coord">Lat: ${point.latitude.toFixed(4)}</span><br>
            <span class="popup-coord">Lng: ${point.longitude.toFixed(4)}</span><br>
            <span class="popup-coord">Accuracy: ${point.accuracy}m</span><br>
            <span class="popup-coord">Own Report: ${point.is_own_report}</span>
        `;

        // Start point
        if (index === 0) {
            L.marker([point.latitude, point.longitude], {icon: startIcon})
                .addTo(map)
                .bindPopup(`<b>Start</b><br>${popupContent}`);
        } 
        // End point
        else if (index === allPoints.length - 1) {
            L.marker([point.latitude, point.longitude], {icon: endIcon})
                .addTo(map)
                .bindPopup(`<b>Current Location</b><br>${popupContent}`)
                .openPopup(); // Open the latest location by default
                
            // Draw accuracy circle only for the latest coordinate
            if (point.accuracy) {
                L.circle([point.latitude, point.longitude], {
                    radius: point.accuracy,
                    color: '#ef4444',
                    fillColor: '#ef4444',
                    fillOpacity: 0.2,
                    weight: 1,
                    interactive: false
                }).addTo(map);
            }
        }
        // Intermediate points
        else {
            let circleColor = (point.is_own_report === true || point.accuracy > 100) ? "#ef4444" : "#3b82f6"; // Red for filtered, blue for path
            const circle = L.circleMarker([point.latitude, point.longitude], {
                radius: 4,
                fillColor: circleColor,
                color: "#fff",
                weight: 1,
                opacity: 1,
                fillOpacity: 0.8
            }).addTo(map);
            circle.bindPopup(popupContent);
        }
    });
}
