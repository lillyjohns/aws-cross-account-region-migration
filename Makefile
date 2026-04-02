.PHONY: setup tf-source tf-target migrate-ec2 migrate-s3 migrate-rds pre-check post-check compare

setup:
	pip install -r requirements.txt

tf-source:
	cd terraform/source-account && terraform init && terraform apply

tf-target:
	cd terraform/target-account && terraform init && terraform apply

pre-check:
	python3 scripts/validate.py pre -c scripts/config.yaml

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
