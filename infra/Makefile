# One Makefile for all three stacks.  STACK=backup-db|main-db|monitoring|caddy (monitoring is owned by another agent) (omit for all, in dependency order).
# Local = docker-compose.yml (laptop, insecure, no domains).  Prod = docker-compose.prod.yml on EC2 (/opt/talentra/repo/infra) via SSH.
SHELL := bash
STACK ?=
S := $(STACK)

.PHONY: up-local down-local logs-local ps-local up-local-all down-local-all test-local \
        up-prod down-prod logs-prod ps-prod deploy-prod deploy-prod-up net

up-local:       ; bash scripts/stack.sh local up $(S)
down-local:     ; bash scripts/stack.sh local down $(S)
logs-local:     ; bash scripts/stack.sh local logs $(S)
ps-local:       ; bash scripts/stack.sh local ps $(S)
up-local-all:   ; bash scripts/stack.sh local up
down-local-all: ; bash scripts/stack.sh local down
test-local:     ; bash scripts/test-local.sh

# ---- prod (EC2) - never run casually; deploy first ----
deploy-prod:    ; bash scripts/deploy.sh --env     # ssh -> git clone/pull on host (+ copy gitignored .env)
deploy-prod-up: ; bash scripts/deploy.sh --env --up
up-prod:        ; bash scripts/stack.sh prod up $(S)
down-prod:      ; bash scripts/stack.sh prod down $(S)
logs-prod:      ; bash scripts/stack.sh prod logs $(S)
ps-prod:        ; bash scripts/stack.sh prod ps $(S)

# ---- schema (infra/schema: DDL generated from docs/05_data_dictionary.xlsx) ----
.PHONY: schema-gen schema-local schema-prod test-schema
DB ?= talentra
schema-gen:     ; python schema/generate_ddl.py
schema-local:   ; bash schema/apply.sh local $(DB)
schema-prod:    ; bash schema/apply.sh prod      # needs SCHEMA_DSN; never run casually
test-schema:    ; DB=$(DB) TALENTRA_DB=$(DB) bash ../tests/run.sh $(ARGS)

# ---- license / config checks (local, read-only) ----
.PHONY: license-check config-check
# yes/no + days left for both local clusters; never prints the key
license-check:
	@for c in talentra-main-local-crdb1-1 talentra-backup-local-backup-crdb-1; do \
	  s=$$(docker exec $$c curl -s localhost:8080/_status/vars | awk '/^seconds_until_enterprise_license_expiry/ {print int($$2/86400)}'); \
	  if [ -n "$$s" ] && [ "$$s" -gt 0 ]; then echo "$$c: licensed, $$s days left"; else echo "$$c: NO LICENSE (throttling starts after the grace period)"; fi; \
	done
config-check:
	@for f in backup-db main-db caddy monitoring; do for y in docker-compose.yml docker-compose.prod.yml; do \
	  [ -f $$f/$$y ] && (cd $$f && docker compose -f $$y config -q 2>&1 | grep -v -E "variable is not set|WARN" ; true) && echo "ok $$f/$$y"; done; done
