.PHONY: setup infra gen-config pre-check migrate-ec2 migrate-s3 migrate-rds dry-run-all destroy

# ── Setup ─────────────────────────────────────────────────
setup:
	pip install -r requirements.txt

# ── Infrastructure (creates EC2, S3, RDS + IAM/KMS) ──────
infra:
	cd terraform/test-resources && terraform init && terraform apply

gen-config:
	bash scripts/gen-config.sh

# ── Seed test data ────────────────────────────────────────
seed-ec2:
	python3 scripts/seed.py seed-ec2 -c scripts/config.yaml -i $$(terraform -chdir=terraform/test-resources output -raw source_instance_id)

seed-rds:
	@echo "Run manually with your DB password:"
	@echo "  python3 scripts/seed.py seed-rds --db-url \"postgres://admin:<PASS>@$$(terraform -chdir=terraform/test-resources output -raw source_rds_endpoint)/migrationtest\""

# ── Validate ──────────────────────────────────────────────
pre-check:
	python3 scripts/validate.py pre -c scripts/config.yaml

# ── Migrate ───────────────────────────────────────────────
migrate-ec2:
	python3 scripts/migrate_ec2.py -c scripts/config.yaml

migrate-s3:
	python3 scripts/migrate_s3.py -c scripts/config.yaml

migrate-rds:
	python3 scripts/migrate_rds.py -c scripts/config.yaml

dry-run-all:
	python3 scripts/migrate_ec2.py -c scripts/config.yaml --dry-run
	python3 scripts/migrate_s3.py -c scripts/config.yaml --dry-run
	python3 scripts/migrate_rds.py -c scripts/config.yaml --dry-run

# ── Cleanup ───────────────────────────────────────────────
destroy:
	cd terraform/test-resources && terraform destroy
