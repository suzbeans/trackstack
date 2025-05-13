import re
import json
import requests
from bs4 import BeautifulSoup # Kept for potential future use or fallback, though not primary for extraction now
import tldextract
from flask import Flask, request, render_template_string
from flask_socketio import SocketIO, emit
from playwright.sync_api import sync_playwright # Added for Playwright

# --- Configuration ---
# Consider making these configurable if needed
PLAYWRIGHT_TIMEOUT = 60000  # 60 seconds for page load
REQUESTS_TIMEOUT_DDG = 10   # 10 seconds for DuckDuckGo API requests
USER_AGENT = "tracker-audit/1.1" # Updated user agent

# Initialize Flask app and SocketIO
app = Flask(__name__)
socketio = SocketIO(app)

def extract_third_party(url: str) -> list[str]:
    """
    Fetches a URL using Playwright, intercepts network requests,
    and returns a sorted list of unique third-party registrable domains.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")

    try:
        target_domain_info = tldextract.extract(url)
        root_domain = target_domain_info.registered_domain
        if not root_domain:
            print(f"Could not determine root domain for URL: {url}")
            return []
    except Exception as e:
        print(f"Error extracting domain from input URL {url}: {e}")
        return []

    third_party_domains = set()
    print(f"Starting Playwright scan for: {url}")

    with sync_playwright() as p:
        browser = None # Initialize browser to None for robust finally block
        try:
            # Using chromium, but firefox or webkit are also options
            browser = p.chromium.launch(headless=True) # Set headless=False for debugging if needed
            # Use a new context to ensure isolation and allow for custom settings
            context = browser.new_context(
                user_agent=USER_AGENT,
                ignore_https_errors=True # Helps with sites using self-signed or problematic SSL certs
            )
            page = context.new_page()

            # Event handler to capture requests
            def handle_request(request_obj):
                request_url = request_obj.url
                try:
                    # Filter out data URLs as they don't have a registrable domain
                    if request_url.startswith("data:"):
                        return

                    extracted_req_domain_info = tldextract.extract(request_url)
                    req_domain = extracted_req_domain_info.registered_domain
                    
                    if req_domain and req_domain != root_domain and req_domain != "" : # Ensure req_domain is not empty
                        third_party_domains.add(req_domain)
                except Exception as e:
                    # Log or handle domains that cause errors during extraction
                    # print(f"Could not process request URL: {request_url} - {e}")
                    pass # Silently ignore problematic URLs for now

            page.on("request", handle_request)

            print(f"Navigating to {url} with Playwright...")
            # Navigate to the page and wait for network activity to settle.
            # 'networkidle' waits until there are no new network connections for 500 ms.
            # 'load' waits for the load event. 'domcontentloaded' is another option.
            page.goto(url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT)
            print(f"Navigation to {url} complete. Found {len(third_party_domains)} potential third-party domains so far.")

            # You could add additional interactions here if needed, e.g., scrolling to trigger more requests:
            # page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # page.wait_for_timeout(5000) # Wait for new requests to load after scroll

        except Exception as e:
            print(f"Error during Playwright operation for {url}: {e}")
            # Depending on the error, you might want to return an empty list or raise it
        finally:
            if page:
                page.close()
            if context:
                context.close()
            if browser and browser.is_connected():
                browser.close()
            print(f"Playwright browser closed for {url}.")
                
    return sorted(list(third_party_domains))

def lookup_ddg(domains: list[str]) -> list[dict]:
    """
    Fetches DuckDuckGo tracker‐radar metadata for each domain present.
    """
    hits = []
    if not domains:
        return hits

    print(f"Looking up {len(domains)} domains against DuckDuckGo Tracker Radar.")
    for d in domains:
        # Construct the URL for the raw JSON data from DDG's tracker-radar repository
        raw_url = f"https://raw.githubusercontent.com/duckduckgo/tracker-radar/main/domains/US/{d}.json"
        try:
            r = requests.get(raw_url, timeout=REQUESTS_TIMEOUT_DDG, headers={"User-Agent": USER_AGENT})
            if r.status_code == 200:
                data = r.json()
                # Ensure the 'domain' field is present, or use the queried domain 'd'
                if "domain" not in data:
                    data["domain"] = d
                hits.append(data)
                print(f"  Found DDG data for: {d}")
            elif r.status_code == 404:
                print(f"  No DDG data for: {d} (404 Not Found)")
            else:
                print(f"  DDG lookup for {d} failed with status: {r.status_code}")
        except requests.RequestException as e:
            print(f"  Request failed for DDG data of {d}: {e}")
            pass # Continue to the next domain if one fails
    print(f"DDG lookup complete. Found details for {len(hits)} domains.")
    return hits

# Updated HTML_TEMPLATE to include SocketIO integration
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Tracker Scan</title>
    <style>
        :root {
            --accent-color: #045951;
            --background-color: #121212;
            --text-color: #e0e0e0;
            --card-background-color: #1e1e1e;
            --border-color: #2a2a2a;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            background-color: var(--background-color);
            color: var(--text-color);
            line-height: 1.6;
        }

        .navbar {
            background-color: var(--accent-color);
            padding: 1rem;
            text-align: center;
        }

        .navbar h1 {
            color: white;
            margin: 0;
            font-size: 1.8rem;
        }

        .container {
            max-width: 800px;
            margin: 2rem auto;
            padding: 2rem;
            background-color: var(--card-background-color);
            box-shadow: 0 0 15px rgba(0, 0, 0, 0.5);
            border-radius: 8px;
        }

        form {
            margin-bottom: 2rem;
            display: flex;
            gap: 10px;
        }

        input[type="text"] {
            flex-grow: 1;
            padding: 12px;
            border: 1px solid var(--border-color);
            border-radius: 4px;
            font-size: 1rem;
            background-color: var(--card-background-color);
            color: var(--text-color);
        }

        button {
            padding: 12px 25px;
            background-color: var(--accent-color);
            color: white;
            border: none;
            border-radius: 0px 0px 10px 0px;
            cursor: pointer;
            font-size: 1rem;
        }

        button:hover {
            background-color: #033f3d;
        }

        .status {
            margin: 1rem 0;
            padding: 1rem;
            border-radius: 4px;
        }

        .status.error {
            background-color: #ffebee;
            border-left: 5px solid #f44336;
            color: #c62828;
        }

        .results {
            margin-top: 2rem;
        }

        .card {
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            background-color: var(--card-background-color);
        }

        .card h4 {
            margin-top: 0;
            margin-bottom: 0.5rem;
            font-size: 1.2rem;
            color: var(--text-color);
        }

        .card p {
            margin: 0.3rem 0;
            font-size: 0.95rem;
        }

        .card strong {
            color: #bbb;
        }
    </style>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.4/socket.io.min.js"></script>
    <script>
        const socket = io();

        socket.on("tracker_found", (tracker) => {
            addTrackerCard(tracker.domain, tracker.owner, tracker.categories, tracker.cookies);
        });

        function addTrackerCard(domain, owner, categories, cookies) {
            const resultsContainer = document.getElementById("results");
            const card = document.createElement("div");
            card.className = "card";

            card.innerHTML = `
                <h4>${domain}</h4>
                <p><strong>Owner:</strong> ${owner || "Unknown"}</p>
                <p><strong>Categories:</strong> ${categories || "N/A"}</p>
                <p><strong>Cookies Used:</strong> ${cookies || "N/A"}</p>
            `;

            resultsContainer.appendChild(card);
        }
    </script>
</head>
<body>
    <div class="navbar">
        <h1>Hoppian Tracker Scanner</h1>
    </div>
    <div class="container">
        <form method="POST" id="scanForm">
            <input type="text" id="url" name="url" placeholder="Enter a URL (e.g., example.com)" required>
            <button type="submit">Scan</button>
        </form>

        <div id="loader" style="display: none;" class="status info">Scanning...</div>

        {% if error %}
            <div class="status error"><p>{{ error }}</p></div>
        {% endif %}

        <div id="results" class="results">
            <!-- Cards will be dynamically added here -->
        </div>
    </div>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    error = None
    if request.method == "POST":
        url_to_scan = request.form.get("url", "").strip()
        if not url_to_scan:
            error = "URL cannot be empty. Please enter a URL."
        else:
            socketio.start_background_task(scan_url, url_to_scan)
    return render_template_string(HTML_TEMPLATE, error=error)

def scan_url(url):
    try:
        third_party_domains = extract_third_party(url)
        trackers = lookup_ddg(third_party_domains)
        for tracker in trackers:
            socketio.emit("tracker_found", {
                "domain": tracker.get("domain", "Unknown"),
                "owner": tracker.get("owner", {}).get("name", "Unknown"),
                "categories": ", ".join(tracker.get("categories", [])),
                "cookies": tracker.get("cookies", "N/A"),
            })
    except Exception as e:
        print(f"Error scanning URL {url}: {e}")

if __name__ == "__main__":
    print("Starting Flask application with SocketIO...")
    socketio.run(app, host="0.0.0.0", port=5005)