#!/usr/bin/env python3
import json, os, re, csv, io
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

sa_info = json.loads(os.environ["GDRIVE_SERVICE_ACCOUNT_JSON"])
creds = service_account.Credentials.from_service_account_info(
    sa_info, scopes=["https://www.googleapis.com/auth/drive.readonly"])
drive = build("drive", "v3", credentials=creds)

# Find renpho-data folder
folder_resp = drive.files().list(
    q="name = 'renpho-data' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
    fields="files(id, name)").execute()
folders = folder_resp.get("files", [])
if not folders:
    raise RuntimeError("Could not find a Google Drive folder named 'renpho-data'")
folder_id = folders[0]["id"]

# Find most recently modified CSV
files_resp = drive.files().list(
    q=f"'{folder_id}' in parents and mimeType = 'text/csv' and trashed = false",
    orderBy="modifiedTime desc", pageSize=1,
    fields="files(id, name, modifiedTime)").execute()
files = files_resp.get("files", [])
if not files:
    raise RuntimeError("No CSV files found in renpho-data folder")
csv_file = files[0]
print(f"Latest CSV: {csv_file['name']} (modified {csv_file['modifiedTime']})")

# Download CSV
buf = io.BytesIO()
request = drive.files().get_media(fileId=csv_file["id"])
downloader = MediaIoBaseDownload(buf, request)
done = False
while not done:
    _, done = downloader.next_chunk()
buf.seek(0)
content = buf.read().decode("utf-8-sig")

# Map internal keys -> possible CSV column names (handles old and new RENPHO formats)
COL_MAP = {
    "date":        ["Date"],
    "weight":      ["Weight(lb)", "Weight (lb)"],
    "bmi":         ["BMI"],
    "bodyfat":     ["Body Fat(%)", "Body Fat (%)", "Body Fat Percentage(%)"],
    "muscle_pct":  ["Skeletal Muscle(%)", "Skeletal Muscle (%)", "Skeletal Muscle Percentage(%)"],
    "fat_free":    ["Fat-Free Mass(lb)", "Fat-Free Mass (lb)"],
    "subcut_fat":  ["Subcutaneous Fat(%)", "Subcutaneous Fat (%)"],
    "water":       ["Body Water(%)", "Body Water (%)", "Body Water Percentage(%)"],
    "muscle_mass": ["Muscle Mass(lb)", "Muscle Mass (lb)"],
    "bmr":         ["BMR(kcal)", "BMR (kcal)"],
}

reader = csv.DictReader(io.StringIO(content))
headers_raw = reader.fieldnames or []
print(f"Headers found: {headers_raw}")

# Resolve which actual header to use for each internal key
col_resolve = {}
missing = []
for key, candidates in COL_MAP.items():
    found = next((c for c in candidates if c in headers_raw), None)
    if found:
        col_resolve[key] = found
    else:
        missing.append(key)

if missing:
    raise RuntimeError(
        f"CSV is missing required columns: {missing}\nHeaders found: {headers_raw}")

# Parse rows
rows = []
for row in reader:
    date_str = row.get(col_resolve["date"], "").strip()
    if not date_str:
        continue
    parsed = None
    for fmt in ("%m/%d/%y", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            parsed = datetime.strptime(date_str, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        continue
    dt = parsed

    def g(key):
        val = row.get(col_resolve.get(key, ""), "").strip()
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    rows.append({
        "date":        dt.strftime("%-m/%-d/%y"),
        "sort_key":    dt,
        "weight":      g("weight"),
        "bmi":         g("bmi"),
        "bodyfat":     g("bodyfat"),
        "muscle_pct":  g("muscle_pct"),
        "fat_free":    g("fat_free"),
        "subcut_fat":  g("subcut_fat"),
        "water":       g("water"),
        "muscle_mass": g("muscle_mass"),
        "bmr":         g("bmr"),
    })

# Sort oldest → newest
rows.sort(key=lambda r: r["sort_key"])

# Build JS array string
def fmt(v):
    return "null" if v is None else str(v)

js_rows = []
for r in rows:
    js_rows.append(
        f'  [{json.dumps(r["date"])},{fmt(r["weight"])},{fmt(r["bmi"])},'
        f'{fmt(r["bodyfat"])},{fmt(r["muscle_pct"])},{fmt(r["fat_free"])},'
        f'{fmt(r["subcut_fat"])},{fmt(r["water"])},{fmt(r["muscle_mass"])},{fmt(r["bmr"])}]'
    )
new_builtin = "const BUILTIN = [\n" + ",\n".join(js_rows) + "\n];"

# Read dashboard HTML
html_path = "body-composition-dashboard_2.html"
with open(html_path, "r", encoding="utf-8") as f:
    old_html = f.read()

# Replace BUILTIN block
pattern = r"const BUILTIN\s*=\s*\[[\s\S]*?\];"
if not re.search(pattern, old_html):
    raise RuntimeError("Could not find 'const BUILTIN = [...];' in the HTML file")

new_html = re.sub(pattern, new_builtin, old_html)

# Idempotency check
if new_html == old_html:
    print("No data change detected — skipping commit.")
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write("DATA_CHANGED=false\n")
else:
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(new_html)
    latest_date = rows[-1]["date"] if rows else "unknown"
    row_count = len(rows)
    print(f"Updated dashboard: {row_count} rows, latest {latest_date}")
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write("DATA_CHANGED=true\n")
        f.write(f"ROW_COUNT={row_count}\n")
        f.write(f"LATEST_DATE={latest_date}\n")
