#!/bin/bash
set -e
cd /Users/dejtech/dejtech/SCBX/CardX/POC/aws-cross-account-region-migration
git rm -r terraform/_old-source-account terraform/_old-target-account 2>/dev/null || true
git add -A
git commit -m "Clean up: remove old terraform dirs, add test-resources + seed/validate"
git push -u origin main --force
echo "✅ Pushed to GitHub"
