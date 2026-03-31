#!/bin/bash
# Run this ONCE on your GCP VM to set up SSH key for GitHub Actions
# Usage: bash setup_gcp_ssh.sh

set -e

KEY_FILE="$HOME/.ssh/github_actions"

echo "=== Generating SSH key for GitHub Actions ==="
ssh-keygen -t ed25519 -f "$KEY_FILE" -N "" -C "github-actions-deploy"

echo ""
echo "=== Adding public key to authorized_keys ==="
cat "$KEY_FILE.pub" >> "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"

echo ""
echo "=== Done! Now add these to GitHub repo secrets ==="
echo "Go to: https://github.com/GaganChhabra25/stock-analyzer/settings/secrets/actions"
echo ""
echo "--- SERVER_SSH_KEY (copy everything below including the header/footer lines) ---"
cat "$KEY_FILE"
echo "--- END OF KEY ---"
echo ""
echo "--- SERVER_HOST ---"
curl -s ifconfig.me
echo ""
echo ""
echo "--- SERVER_USER ---"
whoami
