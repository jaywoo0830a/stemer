#!/usr/bin/env bash
# ============================================================
# bootstrap.sh — 베어메탈 서버 Ubuntu 설치 직후 자동 실행 스크립트
#   호스팅 콘솔의 "Post-Installation Script" 칸에 그대로 붙여넣기
#   (root 권한으로 실행됨)
#
#   하는 일: 패키지 업데이트 / 기본 도구 설치 / 방화벽(SSH만)
#            / 일반 사용자 stemer 생성(+SSH 키 복사)
#   안 하는 일: llama.cpp 빌드·모델 다운로드(29GB)는 첫 로그인 후
#            프로젝트를 옮기고 ./init.sh 로 실행 (설치 시한 초과 방지)
#
#   ※ 베어메탈 콘솔에 붙여넣는 독립 스크립트이므로 lib.sh 를
#      source 하지 않고 로컬 헬퍼(done/step)만 사용한다.
# ============================================================
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a   # apt 업그레이드 중 "서비스 재시작?" 프롬프트 자동 처리 (무인 설치 필수)

step() { printf '>>> %s\n' "$1"; }
done_() { printf '   ✅ %s\n' "$1"; }

step "[1/4] 시스템 업데이트"
apt-get update -y
apt-get upgrade -y

step "[2/4] 기본 도구 설치"
apt-get install -y \
  build-essential cmake git curl wget ca-certificates \
  htop tmux unzip ufw openssh-server

step "[3/4] 방화벽 설정 (SSH만 허용)"
ufw allow OpenSSH
ufw --force enable

step "[4/4] 일반 사용자 stemer 생성 (sudo 그룹)"
if ! id -u stemer >/dev/null 2>&1; then
  useradd -m -s /bin/bash -G sudo stemer
  done_ "사용자 stemer 생성 완료"
else
  done_ "사용자 stemer 이미 존재"
fi

# 콘솔에 등록한 SSH 공개키를 root 계정에서 stemer 계정으로도 복사
# (콘솔에서 키를 root가 아닌 다른 계정에 등록했다면 이 블록은 불필요할 수 있음)
if [[ -f /root/.ssh/authorized_keys ]]; then
  mkdir -p /home/stemer/.ssh
  cp /root/.ssh/authorized_keys /home/stemer/.ssh/authorized_keys
  chown -R stemer:stemer /home/stemer/.ssh
  chmod 700 /home/stemer/.ssh
  chmod 600 /home/stemer/.ssh/authorized_keys
  done_ "root의 SSH 키를 stemer 계정으로 복사 완료"
fi

echo
printf '%s\n' "✅ 부트스트랩 완료. 첫 로그인 후:"
printf '   1) 프로젝트 전송:  rsync -av ~/projects/stemer/ stemer@<서버IP>:~/stemer/\n'
printf '      (또는 git clone <저장소>)\n'
printf '   2) cd ~/stemer && ./init.sh   # 빌드 + 29GB 모델 다운로드 (1회)\n'
printf '   3) ./up.sh                    # 서버 시작\n'
