#!/usr/bin/env python3
"""
Deploy the ha_raw_history custom integration to the HA instance.

Usage:
  python3 deploy.py                    # Deploy to default HA IP
  python3 deploy.py 192.168.214.159    # Deploy to specific IP

What it does:
  1. SCP the custom_components/ha_raw_history/ directory to HA
  2. Ensures 'ha_raw_history:' is in configuration.yaml
  3. Updates all conversation subentries to include 'raw_history' API
  4. Restarts HA core
  5. Verifies the integration loaded
"""

import json
import os
import subprocess
import sys
import time

DEFAULT_HA_HOST = "192.168.214.159"
HA_SSH_USER = "root"
HA_CONFIG_DIR = "/config"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COMPONENT_DIR = os.path.join(SCRIPT_DIR, "custom_components", "ha_raw_history")


def run(cmd, timeout=30, check=True):
    """Run a local command and return output."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        print(f"  ERROR: {result.stderr.strip()}")
        raise SystemExit(1)
    return result.stdout.strip()


def ssh(host, command, timeout=15):
    """Run a command on the HA instance via SSH."""
    cmd = [
        "ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
        f"{HA_SSH_USER}@{host}", command
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HA_HOST

    print(f"=== HA Raw History Deploy Tool ===")
    print(f"Target: {host}")
    print()

    # Step 1: Verify the component directory exists
    if not os.path.isdir(COMPONENT_DIR):
        print(f"ERROR: Component directory not found: {COMPONENT_DIR}")
        print("Run this script from the repository root (where custom_components/ lives).")
        sys.exit(1)

    print(f"Component: {COMPONENT_DIR}")
    print()

    # Step 2: SCP the component to HA
    print("[1/5] Copying custom_components/ha_raw_history/ to HA...")
    result = subprocess.run([
        "scp", "-o", "ConnectTimeout=5", "-r",
        COMPONENT_DIR,
        f"{HA_SSH_USER}@{host}:{HA_CONFIG_DIR}/custom_components/ha_raw_history/"
    ], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"  ERROR: SCP failed: {result.stderr.strip()}")
        sys.exit(1)
    print("  Done.")

    # Step 3: Verify the files arrived
    print("[2/5] Verifying files on HA...")
    ret, out, err = ssh(host, f"ls -la {HA_CONFIG_DIR}/custom_components/ha_raw_history/")
    if ret != 0:
        print(f"  ERROR: Files not found: {err}")
        sys.exit(1)
    print(f"  Files: {out}")

    # Step 4: Add to configuration.yaml if not present
    print("[3/5] Ensuring ha_raw_history: in configuration.yaml...")
    ret, out, err = ssh(host, f"grep -c '^ha_raw_history:' {HA_CONFIG_DIR}/configuration.yaml")
    if ret != 0 or out == "0":
        ret2, out2, err2 = ssh(host, f"echo '' >> {HA_CONFIG_DIR}/configuration.yaml && echo 'ha_raw_history:' >> {HA_CONFIG_DIR}/configuration.yaml")
        if ret2 != 0:
            print(f"  ERROR: Could not append to configuration.yaml: {err2}")
            sys.exit(1)
        print("  Added 'ha_raw_history:' to configuration.yaml.")
    else:
        print("  'ha_raw_history:' already present in configuration.yaml.")

    # Step 5: Update conversation agent config entries to include raw_history API
    print("[4/5] Updating conversation agent API selection...")
    update_script = '''
import json

path = "/config/.storage/core.config_entries"
with open(path) as f:
    data = json.load(f)

changed = False
for entry in data["data"]["entries"]:
    for sub in entry.get("subentries", []):
        if sub.get("subentry_type") == "conversation":
            api_list = sub["data"].get("llm_hass_api", [])
            if isinstance(api_list, list) and "raw_history" not in api_list:
                api_list.append("raw_history")
                sub["data"]["llm_hass_api"] = api_list
                changed = True
                print(f"  Updated {sub.get('title', 'unnamed')}: {api_list}")

if changed:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print("  Config entries saved.")
else:
    print("  No updates needed.")
'''
    tmp_script = "/tmp/update_llm_api.py"
    ret, out, err = ssh(host, f"python3 << 'PYSCRIPT'\n{update_script}\nPYSCRIPT")
    print(out)
    if err:
        print(f"  stderr: {err}")

    # Step 6: Restart HA
    print("[5/5] Restarting Home Assistant...")
    ret, out, err = ssh(host, "ha core restart", timeout=5)
    print(f"  Restart command: {out}")

    # Wait for HA to come back
    print("  Waiting for HA to restart...")
    time.sleep(5)
    for attempt in range(12):
        ret, out, err = ssh(host, "ha core info 2>/dev/null | grep -E 'state|version'", timeout=10)
        if ret == 0:
            print(f"  HA status: {out}")
            break
        print(f"  Waiting... (attempt {attempt + 1}/12)")
        time.sleep(5)
    else:
        print("  WARNING: HA did not report ready within 60 seconds.")
        print("  Check manually with: ha core info")

    print()
    print("=== Deployment Complete ===")
    print()
    print("Next steps:")
    print("  1. Go to Settings > Devices & Services > OpenRouter")
    print("  2. For each conversation agent subentry:")
    print("     Click Configure -> verify 'Raw History' is listed under APIs")
    print("  3. Test: ask 'Was the curtain open at 3pm yesterday?'")
    print()
    print("To remove old python_script files (optional):")
    print(f"  ssh {HA_SSH_USER}@{host} 'rm {HA_CONFIG_DIR}/python_scripts/get_raw_history.py'")
    print(f"  ssh {HA_SSH_USER}@{host} 'rm {HA_CONFIG_DIR}/packages/get_raw_history.yaml'")


if __name__ == "__main__":
    main()
