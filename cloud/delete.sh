#!/bin/bash
# 인스턴스를 삭제해서 과금을 중지한다.
set -euo pipefail

ZONE="asia-northeast3-a"
INSTANCE_NAME="auto-trading-scanner"

gcloud compute instances delete "$INSTANCE_NAME" --zone="$ZONE"
