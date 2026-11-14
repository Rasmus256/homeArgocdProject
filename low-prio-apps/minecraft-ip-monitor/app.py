import ipaddress
import logging
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
BASELINE_FILE = os.environ.get("BASELINE_FILE", "/data/baseline-ip")
IP_LOOKUP_URL = os.environ.get("IP_LOOKUP_URL", "https://api.ipify.org")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
state_lock = threading.Lock()

try:
    with open(BASELINE_FILE, encoding="ascii") as state_file:
        initial_ip = str(ipaddress.ip_address(state_file.read().strip()))
except FileNotFoundError:
    initial_ip = None
except (OSError, ValueError) as error:
    logging.error("Could not load stored baseline IP: %s", error)
    initial_ip = None

current_ip = None
last_error = None


def lookup_ip():
    with urlopen(IP_LOOKUP_URL, timeout=10) as response:
        value = response.read(64).decode("ascii").strip()
    return str(ipaddress.ip_address(value))


def save_baseline(ip_address):
    directory = os.path.dirname(BASELINE_FILE) or "."
    fd, temporary_path = tempfile.mkstemp(prefix=".baseline-ip-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as state_file:
            state_file.write(ip_address + "\n")
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, BASELINE_FILE)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def refresh_ip():
    global initial_ip, current_ip, last_error
    try:
        observed_ip = lookup_ip()
    except Exception as error:
        with state_lock:
            last_error = str(error)
        logging.warning("External IP lookup failed: %s", error)
        return False

    with state_lock:
        current_ip = observed_ip
        last_error = None
        if initial_ip is None:
            try:
                save_baseline(observed_ip)
            except OSError as error:
                last_error = f"could not save baseline IP: {error}"
                logging.exception("Could not save baseline IP")
                return False
            initial_ip = observed_ip
            logging.info("Recorded initial external IP: %s", initial_ip)
        elif observed_ip != initial_ip:
            logging.error("External IP changed from %s to %s", initial_ip, observed_ip)
    return True


def poll_forever():
    while True:
        refresh_ip()
        time.sleep(CHECK_INTERVAL_SECONDS)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/livez":
            self.respond(200, "alive\n")
            return
        if self.path != "/healthz":
            self.respond(404, "not found\n")
            return

        # Run an up-to-date check when probed, in addition to the background poll.
        refresh_ip()
        with state_lock:
            baseline = initial_ip
            observed = current_ip
            error = last_error

        if error:
            self.respond(500, f"external IP check failed: {error}\n")
        elif baseline is None:
            self.respond(500, "external IP baseline is not initialized\n")
        elif observed != baseline:
            self.respond(500, f"external IP changed: expected {baseline}, observed {observed}\n")
        else:
            self.respond(200, f"external IP unchanged: {observed}\n")

    def respond(self, status, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format_string, *args):
        logging.info("%s - %s", self.client_address[0], format_string % args)


if __name__ == "__main__":
    if CHECK_INTERVAL_SECONDS < 1:
        raise ValueError("CHECK_INTERVAL_SECONDS must be at least 1")
    threading.Thread(target=poll_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
