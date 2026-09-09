#!/bin/bash
# GCE 인스턴스 최초 부팅 시 1회 실행되는 스크립트.
# Docker만 설치해두고, 실제 애플리케이션은 Docker Hub 이미지로 받는다
# (코드를 scp로 옮기던 예전 방식에서 컨테이너 배포로 전환함).
set -euo pipefail

apt-get update -y
apt-get install -y docker.io

mkdir -p /opt/auto-trading   # 컨테이너의 /data로 마운트, signals_history.csv 등이 여기 쌓임
mkdir -p /var/log/auto-trading

touch /opt/auto-trading/.ready
