import subprocess
import json
import urllib.request
import sys

def get_token():
    try:
        cmd = ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com", "--query", "accessToken", "-o", "tsv"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception as e:
        print(f"Token Error: {e}", file=sys.stderr)
        return None

token = get_token()
if not token:
    sys.exit(1)

url = "https://api.fabric.microsoft.com/v1/workspaces"
headers = {"Authorization": f"Bearer {token}"}
req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req) as resp:
        print(json.dumps(json.loads(resp.read()), indent=2))
except Exception as e:
    print(f"API Error: {e}", file=sys.stderr)
