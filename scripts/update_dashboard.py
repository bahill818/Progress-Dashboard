#!/usr/bin/env python3
"""
Reads the most recently modified CSV from the renpho-data Google Drive folder
and patches the BUILTIN data array inside body-composition-dashboard_2.html.
"""
import json
import os
import re
import csv
import io
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Auth
sa_info = json.loads(os.environ["GDRIVE_SERVICE_ACCOUNT_JSON"])
creds = service_account.Credentials.from_service_account_info(
    sa_info,
    scopes=["https://www.googleapis.com/auth/drive.readonly"],
)
drive = build("drive", "v3", credentials=creds)

# Find renpho-data folder
folder_resp = drive.files().list(
    q="name = 'renpho-data' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
    fields="files(id, name)",
).execute()
folders = folder_resp.get("files", [])
if not folders:
    raise RuntimeError("Could not find a Google Drive folder named 'renpho-data'")
folder_id = folders[0]["id"]
print(f"Found folder: {folders[0]['name']} ({folder_id})")

# Find the most recently modified CSV
files_resp = drive.files().list(
    q=f"'{folder_id}' in parents and mimeType = 'text/csv' and trashed = false",
    orderBy="modifiedTime desc",
    pageSize=1,
    fields="files(id, name, modifiedTime)",
).execute()
files = files_resp.get("files", [])
if not files:
    raise RuntimeError("No CSV files found in renpho-data folder")
csv_file = files[0]
print(f"Latest CSV: {csv_file['name']} (modified {csv_file['modifiedTime']})")

# Download CSV
request = drive.files().get_media(fileId=csv_file["id"])
buf = io.BytesIO()
downloader = MediaIoBaseDownload(buf, request)
done = False
while not done:
    _, done = downloader.next_chunk()
raw_csv = buf.getvalue().decode("utf-8-sig")

# Parse CSV
COL_MAP = {
    "date":        ["date"],
    "weight":      ["weight(lb)", "weight (lb)"],
    "bmi":         ["bmi"],
    "bodyfat":     ["body fat(%)", "body fat (%)"],
    "muscle_pct":  ["skeletal muscle(%)", "skeletal muscle (%)"],
    "ffm":         ["fat-free mass(lb)", "fat-free mass (lb)"],
    "subq":        ["subcutaneous fat(%)", "subcutaneous fat (%)"],
    "water":       ["body water(%)", "body water (%)"],
    "muscle_mass": ["muscle mass(lb)", "muscle mass (lb)"],
    "bmr":         ["bmr(kcal)", "bmr (kcal)"],
}

reader = csv.DictReader(io.StringIO(raw_csv))
headers_raw = reader.fieldnames or []
headers_norm = {h.strip().lower(): h for h in headers_raw}

def find_col(aliases):
    for a in aliases:
        if a in headers_norm:
            return headers_norm[a]
    return None

col_lookup = {key: find_col(aliases) for key, aliases in COL_MAP.items()}
missing = [k for k, v in col_lookup.items() if v is None]
if missing:
    raise RuntimeError(f"CSV is missing required columns: {missing}\nHeaders found: {headers_raw}")

rows = []
for row in reader:
    try:
        date_str = row[col_lookup["date"]].strip()
        dt = datetime.strptime(date_str, "%m/%d/%y")
        entry = {
            "date":        dt.strftime("%-m/%-d/%y"),
            "weight":      float(row[col_lookup["weight"]]),
            "bmi":         float(row[col_lookup["bmi"]]),
            "bodyfat":     float(row[col_lookup["bodyfat"]]),
            "muscle_pct":  float(row[col_lookup["muscle_pct"]]),
            "ffm":         float(row[col_lookup["ffm"]]),
            "subq":        float(row[col_lookup["subq"]]),
            "water":       float(row[col_lookup["water"]]),
            "muscle_mass": float(row[col_lookup["muscle_mass"]]),
            "bmr":         float(row[col_lookup["bmr"]]),
        }
        rows.append((dt, entry))
    except (ValueError, KeyError):
        continue

if not rows:
    raise RuntimeError("No valid data rows found in CSV")

rows.sort(key=lambda x: x[0])
data = [r[1] for r in rows]
latest_date = data[-1]["date"]
print(f"Parsed {len(data)} valid rows, latest: {latest_date}")

# Patch HTML file
new_json = json.dumps(data, separators=(",", ":"))
new_builtin = f"const BUILTIN = {new_json};"

html_path = "body-composition-dashboard_2.html"
with open(html_path, "r", encoding="utf-8") as f:
    html = f.read()

pattern = r"const BUILTIN\s*=\s*\[[\s\S]*?\];"
if not re.search(pattern, html):
    raise RuntimeError("Could not find 'const BUILTIN = [...];' in HTML file")

new_html = re.sub(pattern, new_builtin, html)

# Idempotency check
github_env = os.environ.get("GITHUB_ENV", "")
if new_html == html:
    print("Data unchanged — skipping commit")
    if github_env:
        with open(github_env, "a") as f:
            f.write("DATA_CHANGED=false\n")
else:
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(new_html)
    print("HTML updated successfully")
    if github_env:
        with open(github_env, "a") as f:
            f.write("DATA_CHANGED=true\n")
            f.write(f"ROW_COUNT={len(data)}\n")
            f.write(f"LATEST_DATE={latest_date}\n")
