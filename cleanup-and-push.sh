#!/bin/bash
set -e
cd "$(dirname "$0")"

rm -rf terraform/
git add -A
git commit -m "Replace Terraform with CloudFormation: source and target stacks"
git push origin main

echo "✅ Done"
