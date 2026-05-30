from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import json
import os
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import re
import time
from fuzzywuzzy import fuzz
from urllib.parse import urljoin, urlparse

app = Flask(__name__)
CORS(app)

# ========= CONFIG =========
API_KEY = os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6K2SEA7Vj2hsoSZrISpxvU5yiu64OcFoIY30mTfDk9EoA")
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash-lite")

RESULTS_FILE = "results.json"

# ========= HELPERS =========

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}

def fetch_page(url, timeout=10):
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        return res.text
    except Exception as e:
        print(f"  ⚠️ Failed to fetch {url}: {e}")
        return None

def clean_html(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:3000]

def find_relevant_links(base_url, html):
    soup = BeautifulSoup(html, "html.parser")
    keywords = ["about", "contact", "services", "team", "company", "product", "solution"]
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text().lower().strip()
        for kw in keywords:
            if fuzz.partial_ratio(kw, href) > 75 or fuzz.partial_ratio(kw, text) > 75:
                full_url = urljoin(base_url, a["href"])
                if urlparse(full_url).netloc == urlparse(base_url).netloc:
                    if full_url not in found:
                        found.append(full_url)
                break
    return found[:4]

def scrape_company(url):
    combined_text = ""
    html = fetch_page(url)
    if not html:
        return ""
    combined_text += clean_html(html) + "\n\n"
    links = find_relevant_links(url, html)
    for link in links:
        time.sleep(1)
        sub_html = fetch_page(link)
        if sub_html:
            combined_text += clean_html(sub_html) + "\n\n"
    return combined_text[:6000]

def ask_gemini(text, url):
    prompt = f"""
You are a business research assistant. Analyze the website content below and extract information.

WEBSITE URL: {url}

WEBSITE CONTENT:
{text}

Return ONLY a valid JSON object with these exact keys. No extra text, no markdown, no explanation:

{{
  "website_name": "Short display name of the website",
  "company_name": "Full legal or official company name",
  "address": "Full physical address if found, else empty string",
  "mobile_number": "Phone number if found, else empty string",
  "mail": ["list", "of", "email", "addresses", "found"],
  "core_service": "One sentence describing their main product or service",
  "target_customer": "Who are their ideal customers",
  "probable_pain_point": "What business problem do their customers likely face",
  "outreach_opener": "A personalized 2-sentence sales opener mentioning the company by name"
}}

STRICT RULES:
- If a field is not found, use empty string or empty list
- NEVER invent contact details
- Return ONLY the JSON object
"""
    for attempt in range(3):
        try:
            response = model.generate_content(prompt)
            raw = response.text.strip()
            raw = re.sub(r'^```json', '', raw).strip()
            raw = re.sub(r'^```', '', raw).strip()
            raw = re.sub(r'```$', '', raw).strip()
            return json.loads(raw)
        except Exception as e:
            if "429" in str(e):
                wait = (attempt + 1) * 10
                print(f"  ⏳ Rate limited. Waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  ⚠️ Gemini error: {e}")
                break

    return {
        "website_name": url, "company_name": "", "address": "",
        "mobile_number": "", "mail": [], "core_service": "",
        "target_customer": "", "probable_pain_point": "", "outreach_opener": ""
    }

def enrich_company(url: str) -> dict:
    page_text = scrape_company(url)
    if not page_text.strip():
        return {
            "website_name": url, "company_name": "", "address": "",
            "mobile_number": "", "mail": [], "core_service": "",
            "target_customer": "", "probable_pain_point": "", "outreach_opener": ""
        }
    return ask_gemini(page_text, url)

def load_results():
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, "r") as f:
            return json.load(f)
    return []

def save_results(results):
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)

# ========= ROUTES =========

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/enrich", methods=["POST"])
def enrich():
    data = request.json
    url = data.get("url", "").strip()
    website_name = data.get("website_name", "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400

    print(f"\n🏢 Enriching: {url}")
    result = enrich_company(url)

    # Add website name from UI input if provided
    if website_name:
        result["website_name"] = website_name

    # Save to results.json
    results = load_results()
    results.append(result)
    save_results(results)

    return jsonify(result)

@app.route("/results", methods=["GET"])
def get_results():
    return jsonify(load_results())

# ========= RUN =========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)