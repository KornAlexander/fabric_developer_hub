import sys, json, base64, time, requests, subprocess

def get_token():
    return subprocess.check_output(["az", "account", "get-access-token", "--resource", "https://analysis.windows.net/powerbi/api", "--query", "accessToken", "-o", "tsv"]).decode("utf-8").strip()

def decode(b64):
    return base64.b64decode(b64).decode("utf-8")

report_id = "032d4678-07a1-4b3d-872b-b1ae8c1f11ab"
workspace_id = "8bdca8af-1db1-4fd8-9564-0c98b4dbdffc"
token = get_token()
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

resp = requests.post(f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/reports/{report_id}/getDefinition", headers=headers, json={})

if resp.status_code == 202:
    loc = resp.headers.get("Location")
    while True:
        r = requests.get(loc, headers=headers).json()
        if r.get("status") == "Succeeded":
            definition = r["result"]["definition"]
            break
        elif r.get("status") == "Failed":
            print("Failed")
            sys.exit(1)
        time.sleep(5)
else:
    definition = resp.json()["definition"]

parts = definition.get("parts", [])
summary = {"paths": [p.get("path") for p in parts]}
for p in parts:
    path = p.get("path")
    payload = p.get("payload")
    if not payload: continue
    content = decode(payload)
    try:
        parsed = json.loads(content)
    except:
        parsed = content
    if path == "definition.pbir" or path == "definition/pages/pages.json" or path == "report.json" or "page.json" in path or "visual.json" in path:
        summary[path] = parsed

print(json.dumps(summary, indent=2))
