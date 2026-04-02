.PHONY: setup infra gen-config destroy

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
		--capabilities CAPABILITY_NAMED_IAM \
		--profile target-account --region $(TARGET_REGION) \
		--parameter-overrides \
			SourceAccountId=$(shell aws sts get-caller-identity --profile source-account --query Account --output text)

gen-config:
	bash scripts/gen-config.sh

# ── EC2 ───────────────────────────────────────────────────
ec2-prepare:
	python3 -m services.ec2.prepare -c scripts/config.yaml

ec2-migrate:
	python3 -m services.ec2.migrate -c scripts/config.yaml

ec2-migrate-dry:
	python3 -m services.ec2.migrate -c scripts/config.yaml --dry-run

# ── S3 ────────────────────────────────────────────────────
s3-prepare:
	python3 -m services.s3.prepare -c scripts/config.yaml

s3-migrate:
	python3 -m services.s3.migrate -c scripts/config.yaml

s3-migrate-dry:
	python3 -m services.s3.migrate -c scripts/config.yaml --dry-run

s3-verify:
	python3 -m services.s3.verify -c scripts/config.yaml

# ── RDS ───────────────────────────────────────────────────
rds-prepare:
	python3 -m services.rds.prepare -c scripts/config.yaml --db-password $(DB_PASSWORD)

rds-migrate:
	python3 -m services.rds.migrate -c scripts/config.yaml

rds-migrate-dry:
	python3 -m services.rds.migrate -c scripts/config.yaml --dry-run

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
