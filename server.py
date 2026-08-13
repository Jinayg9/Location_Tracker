import http.server
import socketserver
import os
import json

PORT = 8000
CSV_FILE = 'tracking_log.csv'

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/clear':
            # Clear the tracking_log.csv file, leaving only the header
            try:
                with open(CSV_FILE, 'w') as f:
                    f.write('timestamp,latitude,longitude,accuracy,is_own_report,type\n')
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "CSV cleared"}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
        else:
            self.send_error(404, "Endpoint not found")

socketserver.ThreadingTCPServer.allow_reuse_address = True
with socketserver.ThreadingTCPServer(("", PORT), CustomHandler) as httpd:
    print(f"Serving at port {PORT}")
    httpd.serve_forever()
