.PHONY: setup infra gen-config pre-check migrate-ec2 migrate-s3 migrate-rds dry-run-all destroy

STACK_NAME    = cross-account-migration
SOURCE_REGION = ap-southeast-1
TARGET_REGION = ap-southeast-7

# ── Setup ─────────────────────────────────────────────────
setup:
	pip install -r requirements.txt

# ── Infrastructure ────────────────────────────────────────
infra:
	@echo "Deploying source stack in $(SOURCE_REGION)..."
	aws cloudformation deploy \
		--template-file cfn/source-stack.yaml \
		--stack-name $(STACK_NAME)-source \
		--capabilities CAPABILITY_NAMED_IAM \
		--profile source-account --region $(SOURCE_REGION) \
		--parameter-overrides \
			TargetAccountId=$(TARGET_ACCOUNT_ID) \
			DBPassword=$(DB_PASSWORD)
	@echo "Deploying target stack in $(TARGET_REGION)..."
	aws cloudformation deploy \
		--template-file cfn/target-stack.yaml \
		--stack-name $(STACK_NAME)-target \
		--profile target-account --region $(TARGET_REGION) \
		--parameter-overrides \
			SourceAccountId=$(shell aws sts get-caller-identity --profile source-account --query Account --output text)

gen-config:
	bash scripts/gen-config.sh

# ── Seed test data ────────────────────────────────────────
seed-ec2:
	python3 scripts/seed.py seed-ec2 -c scripts/config.yaml

seed-rds:
	@echo "Run manually with your DB password:"
	@echo "  python3 scripts/seed.py seed-rds --db-url \"postgres://admin:<PASS>@<RDS_ENDPOINT>/migrationtest\""

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
	@echo "Deleting source stack..."
	aws cloudformation delete-stack \
		--stack-name $(STACK_NAME)-source \
		--profile source-account --region $(SOURCE_REGION)
	aws cloudformation wait stack-delete-complete \
		--stack-name $(STACK_NAME)-source \
		--profile source-account --region $(SOURCE_REGION)
	@echo "Deleting target stack..."
	aws cloudformation delete-stack \
		--stack-name $(STACK_NAME)-target \
		--profile target-account --region $(TARGET_REGION)
	aws cloudformation wait stack-delete-complete \
		--stack-name $(STACK_NAME)-target \
		--profile target-account --region $(TARGET_REGION)
	@echo "✅ All stacks deleted"
