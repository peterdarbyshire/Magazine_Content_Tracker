"""
Magazine New Content Tracker
------------------------------
Visits a list of magazine/podcast homepages, extracts candidate story links,
compares them against what was seen on the previous run (persisted in
seen_content.json, committed back to the repo), and emails a digest of
NEW fiction/flash/micro-fiction content only (poetry and art excluded).

Required environment variables (set as GitHub Actions secrets):
    ANTHROPIC_API_KEY   - your Anthropic API key
    EMAIL_ADDRESS       - the Gmail address sending the email
    EMAIL_APP_PASSWORD  - a Gmail "app password" (not your normal password)
    EMAIL_TO            - the address to send the digest to

Magazine list is defined below in MAGAZINES. Edit that list to add/remove sources.

State persistence: this script reads/writes seen_content.json in the repo
root. The GitHub Actions workflow commits that file back after each run so
the "new since last scan" comparison works across runs. Do not delete this
file, or the next run will treat everything as new again.
"""

import os
import json
import html as html_module
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date

import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic
from playwright.sync_api import sync_playwright

# ---- Configuration ------------------------------------------------------

MAGAZINES = [
    {"name": "Agita Magazine", "url": "https://agitamag.com/"},
    {"name": "Factor Four Magazine", "url": "https://factorfourmag.com/"},
    {"name": "Flash Fiction Online", "url": "https://www.flashfictiononline.com/"},
    {"name": "Flash Point SF", "url": "https://flashpointsf.com/"},
    {"name": "Fractured – Flash Fiction Magazine", "url": "https://fracturedlit.com/"},
    {"name": "Night Shades", "url": "https://www.nightshadesmag.com/"},
    {"name": "Small Wonders", "url": "https://smallwondersmag.com/"},
    {"name": "Bourbon Penn", "url": "https://www.bourbonpenn.com/"},
    {"name": "ergot.", "url": "https://www.ergot.press/"},
    {"name": "Three-Lobed Burning Eye Magazine", "url": "https://www.3lobedmag.com/"},
    {"name": "Trollbreath", "url": "https://magazine.trollbreath.com/"},
    {"name": "Weird Lit", "url": "https://www.weirdlitmag.com/"},
    {"name": "Apex Magazine", "url": "https://www.apexbookcompany.com/a/blog/apex-magazine/"},
    {"name": "Cosmic Horror Monthly", "url": "https://cosmichorrormonthly.com/"},
    {"name": "Nightmare", "url": "https://www.nightmare-magazine.com/"},
    {"name": "The Dark Magazine", "url": "https://www.thedarkmagazine.com/"},
    {"name": "The Fifth Corner", "url": "https://thefifthcorner.ca/"},
    {"name": "Beneath Ceaseless Skies", "url": "https://www.beneath-ceaseless-skies.com/"},
    {"name": "Heroic Fantasy Quarterly", "url": "https://www.heroicfantasyquarterly.com/"},
    {"name": "Lightspeed Magazine", "url": "https://www.lightspeedmagazine.com/"},
    {"name": "Abyss & Apex", "url": "https://www.abyssapexzine.com/"},
    {"name": "Adventitious", "url": "https://www.adventitious.net/"},
    {"name": "Andromeda Spaceways Magazine", "url": "https://andromedaspaceways.com/"},
    {"name": "Augur (Canadian)", "url": "https://augursociety.org/"},
    {"name": "Baubles From Bones", "url": "https://www.baublesfrombones.com/"},
    {"name": "Diabolical Plots", "url": "https://www.diabolicalplots.com/"},
    {"name": "Dirty Magick Magazine", "url": "https://dirtymagickmagazine.com/"},
    {"name": "Electric Spec", "url": "https://electricspec.com/index.html"},
    {"name": "Fusion Fragment (Canadian)", "url": "https://www.fusionfragment.com/"},
    {"name": "Gavagai", "url": "https://gavagai.com/"},
    {"name": "GigaNotoSaurus", "url": "https://giganotosaurus.org/"},
    {"name": "Goblins & Galaxies", "url": "https://goblinsandgalaxies.com/category/fiction/"},
    {"name": "Haven Spec", "url": "https://www.havenspec.com/"},
    {"name": "Kaleidotrope", "url": "https://kaleidotrope.net/"},
    {"name": "MetaStellar", "url": "https://www.metastellar.com/"},
    {"name": "Mysterion", "url": "https://www.mysteriononline.com/"},
    {"name": "New Myths", "url": "https://sites.google.com/newmyths.com/newmyths-com-home/home-page"},
    {"name": "Orion's Belt", "url": "https://www.orions-belt.net/"},
    {"name": "Pulp Literature", "url": "https://pulpliterature.com/"},
    {"name": "Strange Horizons", "url": "http://strangehorizons.com/"},
    {"name": "Strange Pilgrims", "url": "https://www.strangepilgrims.com/"},
    {"name": "Tales & Feathers Magazine (Canadian)", "url": "https://augursociety.org/tales-and-feathers-magazine/"},
    {"name": "The Fabulist", "url": "https://fabulistmagazine.com/"},
    {"name": "The Sunday Morning Transport", "url": "https://www.sundaymorningtransport.com/"},
    {"name": "Tome & Space (CDN)", "url": "https://tomeandspace.com/"},
    {"name": "Translunar Travelers Lounge", "url": "https://translunartravelerslounge.com/"},
    {"name": "Uncanny", "url": "https://www.uncannymagazine.com/"},
    {"name": "Wyldblood", "url": "https://wyldblood.com/"},
    {"name": "Pacific Northwest Gothic", "url": "https://pnwgothic.com/"},
    {"name": "Drabblecast", "url": "https://www.drabblecast.org/"},
    {"name": "Escape Pod", "url": "https://escapepod.org/"},
    {"name": "No Sleep Podcast", "url": "https://www.thenosleeppodcast.com/"},
    {"name": "PodCastle", "url": "https://podcastle.org/"},
    {"name": "PseudoPod", "url": "https://pseudopod.org/"},
    {"name": "Starship Sofa", "url": "https://starshipsofa.com/"},
    {"name": "Tales to Terrify", "url": "https://talestoterrify.com/"},
]

STATE_FILE = "seen_content.json"
MAX_LINKS_PER_SITE = 25       # how many candidate links to consider per site
REQUEST_TIMEOUT = 15
PLAYWRIGHT_TIMEOUT_MS = 20000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---- Fetching -------------------------------------------------------------

def fetch_with_requests(url):
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def fetch_with_playwright(url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(url, timeout=PLAYWRIGHT_TIMEOUT_MS, wait_until="domcontentloaded")
        html_content = page.content()
        browser.close()
        return html_content


def fetch_html(url):
    """Try a plain request first, fall back to a headless browser on failure."""
    try:
        return fetch_with_requests(url), None
    except requests.RequestException as e:
        print(f"  requests fetch failed ({e}), trying Playwright fallback...")
    try:
        return fetch_with_playwright(url), None
    except Exception as e:
        return None, f"requests and Playwright both failed ({e})"


def extract_links(html_content, base_url):
    """Pull candidate content links (title text + href) from a page."""
    soup = BeautifulSoup(html_content, "html.parser")
    seen_urls = set()
    items = []

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]

        if not text or len(text) < 15:
            continue
        if href.startswith(("#", "mailto:", "javascript:")):
            continue

        if href.startswith("/"):
            parts = base_url.split("/")
            base = parts[0] + "//" + parts[2]
            href = base + href
        elif not href.startswith("http"):
            continue

        if href in seen_urls:
            continue
        seen_urls.add(href)

        items.append({"title": text, "url": href})

        if len(items) >= MAX_LINKS_PER_SITE:
            break

    return items


# ---- State persistence -----------------------------------------------------

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---- Filtering new items with Claude ---------------------------------------

def filter_new_fiction(client, magazine_name, new_items):
    """
    Given candidate 'new' links for a magazine, ask Claude which are genuinely
    new FICTION stories (including flash/micro fiction) as opposed to poetry,
    art/illustration, nonfiction, editorials, interviews, news posts, or
    navigation/junk links. Returns a list of {title, url, blurb}.
    """
    if not new_items:
        return []

    listing = "\n".join(f"- {i['title']} ({i['url']})" for i in new_items)

    prompt = f"""You are helping identify newly published FICTION content on
a magazine/podcast website called "{magazine_name}".

Below is a list of links that appeared on the site's homepage/listing page
and were NOT present on the previous scan (i.e. they are new since last
time). Some of these may not actually be new stories — they could be
navigation links, ads, unrelated pages, or non-fiction content.

ONLY include items that are genuinely new FICTION stories — this includes
flash fiction and micro fiction / short-shorts. EXCLUDE poetry, art or
illustration features, nonfiction essays, interviews, editorials/news posts,
"about" or navigation pages, and anything that isn't an actual story.

Candidate new links:
{listing}

Respond with ONLY a JSON array (no other text, no markdown fences) in this
exact format:
[{{"title": "story title", "url": "story url", "blurb": "one-sentence description of what the story is about, based on the title/context"}}]

If none of the candidates are genuine new fiction stories, respond with: []
Do not invent details beyond what's implied by the title/link text.
"""

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = "".join(b.text for b in response.content if b.type == "text").strip()

    # Strip accidental markdown fences, just in case
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        print(f"  [warn] Could not parse Claude's response for {magazine_name}, skipping.")

    return []


# ---- Email ------------------------------------------------------------------

def build_digest_html(results_by_magazine, baseline_magazines):
    today = date.today().strftime("%B %d, %Y")

    def esc(s):
        return html_module.escape(s or "")

    magazines_with_new = sorted(
        (name for name, items in results_by_magazine.items() if items),
        key=lambda n: n.lower(),
    )

    total_new = sum(len(v) for v in results_by_magazine.values())

    sections = []
    for name in magazines_with_new:
        items = results_by_magazine[name]
        entries = "".join(
            f"<p style='margin:0 0 12px 20px;'>"
            f"{esc(item.get('title', 'Untitled'))} — {esc(item.get('blurb', ''))}<br>"
            f"<a href='{esc(item.get('url', ''))}'>{esc(item.get('url', ''))}</a>"
            f"</p>"
            for item in items
        )
        sections.append(
            f"<p style='margin:0 0 4px 0;'><b>{esc(name)}</b></p>{entries}"
        )

    if not sections:
        body_content = "<p>No new fiction, flash, or micro stories found since the last scan.</p>"
    else:
        body_content = "".join(sections)

    baseline_note = ""
    if baseline_magazines:
        names = ", ".join(sorted(baseline_magazines))
        baseline_note = (
            f"<p style='color:#666; font-size:12px;'>Note: first-time scan for: {esc(names)}. "
            f"These establish a baseline and won't show new content until the next scan.</p>"
        )

    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; font-size: 14px; color: #222;">
        <h2>New Fiction Content — {today}</h2>
        <p>{total_new} new stor{"y" if total_new == 1 else "ies"} found across {len(magazines_with_new)} magazine(s).</p>
        {body_content}
        {baseline_note}
      </body>
    </html>
    """


def build_digest_text(results_by_magazine, baseline_magazines):
    today = date.today().strftime("%B %d, %Y")
    lines = [f"New Fiction Content — {today}", ""]

    magazines_with_new = sorted(
        (name for name, items in results_by_magazine.items() if items),
        key=lambda n: n.lower(),
    )

    if not magazines_with_new:
        lines.append("No new fiction, flash, or micro stories found since the last scan.")
    else:
        for name in magazines_with_new:
            lines.append(name)
            for item in results_by_magazine[name]:
                lines.append(f"  {item.get('title', 'Untitled')} — {item.get('blurb', '')}")
                lines.append(f"    {item.get('url', '')}")
            lines.append("")

    if baseline_magazines:
        lines.append("")
        lines.append(f"Note: first-time scan for: {', '.join(sorted(baseline_magazines))}.")
        lines.append("These establish a baseline and won't show new content until the next scan.")

    return "\n".join(lines)


def send_email(subject, plain_text, html_body):
    email_from = os.environ["EMAIL_ADDRESS"]
    email_password = os.environ["EMAIL_APP_PASSWORD"]
    email_to = os.environ["EMAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(email_from, email_password)
        server.sendmail(email_from, email_to, msg.as_string())


# ---- Main ---------------------------------------------------------------

def main():
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    state = load_state()

    results_by_magazine = {}
    baseline_magazines = []
    new_state = dict(state)  # start from previous state, update as we go

    for mag in MAGAZINES:
        name, url = mag["name"], mag["url"]
        print(f"Checking {name}...")

        html_content, error = fetch_html(url)
        if error:
            print(f"  [warn] Could not fetch {name}: {error}")
            continue

        current_items = extract_links(html_content, url)
        current_urls = {item["url"] for item in current_items}

        previously_seen = set(state.get(name, []))

        if name not in state:
            # First time seeing this magazine — establish baseline, don't report.
            print(f"  first scan — establishing baseline ({len(current_urls)} links)")
            baseline_magazines.append(name)
            new_state[name] = list(current_urls)
            continue

        new_urls = current_urls - previously_seen
        new_items = [item for item in current_items if item["url"] in new_urls]

        if new_items:
            print(f"  found {len(new_items)} candidate new link(s), checking with Claude...")
            fiction_items = filter_new_fiction(client, name, new_items)
            print(f"  -> {len(fiction_items)} confirmed new fiction item(s)")
            if fiction_items:
                results_by_magazine[name] = fiction_items
        else:
            print("  no new links since last scan")

        # Update state with the full current set of seen URLs (union, so we
        # never "forget" older links even if a homepage rotates content out).
        new_state[name] = list(previously_seen | current_urls)

    save_state(new_state)

    digest_text = build_digest_text(results_by_magazine, baseline_magazines)
    digest_html = build_digest_html(results_by_magazine, baseline_magazines)

    with open("new_content_output.txt", "w", encoding="utf-8") as f:
        f.write(digest_text)

    today = date.today().strftime("%B %d, %Y")
    send_email(f"New Fiction Content — {today}", digest_text, digest_html)
    print("Digest emailed successfully.")


if __name__ == "__main__":
    main()
