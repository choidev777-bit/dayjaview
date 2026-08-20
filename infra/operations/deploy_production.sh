#!/bin/sh
# DAYJAVIEW production 배포 — 운영자 PC의 Git Bash에서, 저장소 루트에서 실행한다.
#
#   bash infra/operations/deploy_production.sh
#
# 하는 일: ① 커밋된 코드 트리를 VM으로 전송(git archive — VM에 GitHub 자격증명을
# 두지 않는다) ② VM 부트스트랩 ③ .env.local의 키를 VM secret 파일로 주입(값은
# 화면에 출력하지 않음) ④ 인포스탁 번들 전송 ⑤ ARM64 빌드·기동 ⑥ health 대기.
# 여러 번 실행해도 안전하다. 롤백 = 로컬에서 이전 commit checkout 후 재실행.
set -eu

VM="${1:-ubuntu@api.dayjaview.duckdns.org}"
COMPOSE=/opt/dayjaview/repo/infra/deployment/compose.production.yml

say() { printf '\n== %s\n' "$1"; }

[ -f .env.local ] || { echo "저장소 루트(.env.local이 있는 곳)에서 실행하세요." >&2; exit 1; }
git diff --quiet HEAD -- . 2>/dev/null \
    || echo "주의: 커밋 안 된 변경은 배포에 포함되지 않습니다(git archive HEAD 기준)."

# .env.local에서 이름으로 값을 꺼낸다. 값은 어떤 경우에도 echo하지 않는다.
# Windows 편집기의 CRLF 대비로 \r을 벗긴다 — 안 벗기면 값 끝에 \r이 붙는다.
value_of() { sed -n "s/^[[:space:]]*$1=//p" .env.local | tr -d '\r' | tail -1; }
require() {
    v=$(value_of "$1")
    if [ -z "$v" ]; then
        echo ".env.local의 $1 값이 비어 있습니다. 채운 뒤 다시 실행하세요." >&2
        exit 1
    fi
    printf '%s' "$v"
}

say "0) 필수 키 존재 확인 (.env.local — 값은 출력하지 않음)"
GOOGLE_ID=$(require GOOGLE_OAUTH_CLIENT_ID)
GOOGLE_SECRET=$(require GOOGLE_OAUTH_CLIENT_SECRET)
OPERATORS=$(require OPERATOR_BOOTSTRAP_GOOGLE_EMAILS)
KRX=$(require KRX_API_KEY)
OPENDART=$(require OPENDART_API_KEY)
KIWOOM_KEY=$(require KIWOOM_APP_KEY)
KIWOOM_SECRET=$(require KIWOOM_APP_SECRET)
KIWOOM_CONDITIONS=$(value_of KIWOOM_CONDITION_IDS)
RESEARCH_VERIFIED=$(value_of RESEARCH_VERIFIED_QUERY_TYPES)
RESEARCH_UNVERIFIED=$(value_of RESEARCH_SERVE_UNVERIFIED)
RESEARCH_COMPOSE=$(value_of RESEARCH_OPEN_COMPOSE)
NAVER_ID=$(require NAVER_API_HUB_CLIENT_ID)
NAVER_SECRET=$(require NAVER_API_HUB_CLIENT_SECRET)
OPENAI_KEY=$(require OPENAI_API_KEY)
OPENAI_MODEL=$(require OPENAI_MODEL)
OPENAI_REASONING=$(value_of OPENAI_REASONING_EFFORT)
NEWS_RSS=$(value_of NEWS_RSS_SOURCES)
DEPLOY_COMMIT=$(git rev-parse --short=12 HEAD)
echo "필수 11개 모두 있음"

say "1) SSH 연결 확인"
ssh -o ConnectTimeout=10 "$VM" 'echo "VM 접속 OK: $(uname -m) $(lsb_release -ds 2>/dev/null || true)"'

say "2) 코드 전송 (커밋된 트리 전체를 /opt/dayjaview/repo로)"
ssh "$VM" 'sudo mkdir -p /opt/dayjaview/repo && sudo find /opt/dayjaview/repo -mindepth 1 -delete'
git archive HEAD | ssh "$VM" 'sudo tar -x -C /opt/dayjaview/repo'
echo "전송한 commit: $(git rev-parse --short HEAD)"

say "3) VM 부트스트랩 (docker·방화벽·디렉터리·백업 cron — 멱등)"
ssh "$VM" 'sudo bash /opt/dayjaview/repo/infra/operations/vm_bootstrap.sh'

say "4) secret 주입 — VM에서 생성·유지되는 값 (postgres 비밀번호·서명 키·백업 암호문)"
ssh "$VM" 'sudo sh -s' <<'REMOTE'
set -eu
umask 077
cd /etc/dayjaview
if ! grep -q '^POSTGRES_PASSWORD=' postgres.env 2>/dev/null; then
    printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)" > postgres.env
fi
pw=$(sed -n 's/^POSTGRES_PASSWORD=//p' postgres.env | tail -1)
printf 'PGPASSWORD=%s\n' "$pw" > migrate.env
{
    printf 'INFOSTOCK_DATABASE_URL=postgresql://dayjaview:%s@postgres:5432/dayjaview\n' "$pw"
    printf 'NEWS_DATABASE_URL=postgresql://dayjaview:%s@postgres:5432/dayjaview\n' "$pw"
} > worker.env
signing=$(sed -n 's/^SESSION_SIGNING_SECRET=//p' api.env 2>/dev/null | tail -1)
[ -n "$signing" ] || signing=$(openssl rand -hex 32)
{
    printf 'DATABASE_URL=postgresql://dayjaview:%s@postgres:5432/dayjaview\n' "$pw"
    printf 'SESSION_SIGNING_SECRET=%s\n' "$signing"
} > api.env
[ -s backup.passphrase ] || openssl rand -hex 32 > backup.passphrase
chmod 600 postgres.env migrate.env worker.env api.env backup.passphrase
echo "VM 생성분 완료 (값 미출력)"
REMOTE

say "4b) secret 주입 — .env.local 유래 값 append"
{
    printf 'GOOGLE_OAUTH_CLIENT_ID=%s\n' "$GOOGLE_ID"
    printf 'GOOGLE_OAUTH_CLIENT_SECRET=%s\n' "$GOOGLE_SECRET"
    printf 'OPERATOR_BOOTSTRAP_GOOGLE_EMAILS=%s\n' "$OPERATORS"
    printf 'KRX_API_KEY=%s\n' "$KRX"
    printf 'OPENDART_API_KEY=%s\n' "$OPENDART"
    printf 'KIWOOM_MODE=real\n'
    printf 'KIWOOM_APP_KEY=%s\n' "$KIWOOM_KEY"
    printf 'KIWOOM_APP_SECRET=%s\n' "$KIWOOM_SECRET"
    printf 'DAYJAVIEW_DEPLOYMENT_VERSION=%s\n' "$DEPLOY_COMMIT"
    printf 'DAYJAVIEW_COMMIT=%s\n' "$DEPLOY_COMMIT"
    if [ -n "$KIWOOM_CONDITIONS" ]; then
        printf 'KIWOOM_CONDITION_IDS=%s\n' "$KIWOOM_CONDITIONS"
    fi
    # 사람 검수를 통과한 리서치 질의 유형만 연다(계획서 11.1.2). 비면 전부 잠긴다.
    if [ -n "$RESEARCH_VERIFIED" ]; then
        printf 'RESEARCH_VERIFIED_QUERY_TYPES=%s\n' "$RESEARCH_VERIFIED"
    fi
    # "1"이면 검수 전 유형도 답하되 화면에는 검수 전 경고가 남는다.
    if [ -n "$RESEARCH_UNVERIFIED" ]; then
        printf 'RESEARCH_SERVE_UNVERIFIED=%s\n' "$RESEARCH_UNVERIFIED"
    fi
    # "1"이면 복합 질문 LLM 분해를 켠다. 분해기가 쓸 OpenAI 키도 api에 준다.
    if [ -n "$RESEARCH_COMPOSE" ]; then
        printf 'RESEARCH_OPEN_COMPOSE=%s\n' "$RESEARCH_COMPOSE"
        printf 'OPENAI_API_KEY=%s\n' "$OPENAI_KEY"
        printf 'OPENAI_MODEL=%s\n' "$OPENAI_MODEL"
        if [ -n "$OPENAI_REASONING" ]; then
            printf 'OPENAI_REASONING_EFFORT=%s\n' "$OPENAI_REASONING"
        fi
    fi
} | ssh "$VM" 'sudo tee -a /etc/dayjaview/api.env >/dev/null && echo "api.env 완성"'

{
    printf 'NAVER_API_HUB_CLIENT_ID=%s\n' "$NAVER_ID"
    printf 'NAVER_API_HUB_CLIENT_SECRET=%s\n' "$NAVER_SECRET"
    printf 'OPENAI_API_KEY=%s\n' "$OPENAI_KEY"
    printf 'OPENAI_MODEL=%s\n' "$OPENAI_MODEL"
    if [ -n "$OPENAI_REASONING" ]; then
        printf 'OPENAI_REASONING_EFFORT=%s\n' "$OPENAI_REASONING"
    fi
    if [ -n "$NEWS_RSS" ]; then
        printf 'NEWS_RSS_SOURCES=%s\n' "$NEWS_RSS"
    fi
} | ssh "$VM" 'sudo tee -a /etc/dayjaview/worker.env >/dev/null && sudo chmod 600 /etc/dayjaview/worker.env && echo "worker.env 완성"'

say "5) 인포스탁 280테마 번들 전송"
if [ -d data/infostock/import ]; then
    tar -cz -C data/infostock/import . \
        | ssh "$VM" 'sudo tar -xz -C /opt/dayjaview/data/infostock-import \
            && sudo chown -R 10001:10001 /opt/dayjaview/data/infostock-import \
            && echo "번들 반입 완료: $(ls /opt/dayjaview/data/infostock-import | wc -l)개 파일"'
else
    echo "경고: data/infostock/import가 없어 건너뜀 — 거래일에 테마 우주를 못 만든다" >&2
fi

say "5-1) DailyFeaturedTheme 본문 전송 (약 200MB — 첫 회는 수 분 걸린다)"
DAILY_DIR=$(ls -d data/infostock/daily-full-* 2>/dev/null | sort | tail -1 || true)
if [ -n "$DAILY_DIR" ]; then
    ssh "$VM" 'df -h /opt | tail -1'
    tar -cz -C "$DAILY_DIR" . \
        | ssh "$VM" 'sudo tar -xz -C /opt/dayjaview/data/infostock-daily \
            && sudo chown -R 10001:10001 /opt/dayjaview/data/infostock-daily \
            && echo "본문 반입 완료: $(ls /opt/dayjaview/data/infostock-daily/details 2>/dev/null | wc -l)건"'
else
    echo "경고: data/infostock/daily-full-*이 없어 건너뜀 — Daily 사건 질의를 못 만든다" >&2
fi

say "5-2) E-16 가격 corpus 전송 (1.6GB — 크기 같으면 건너뜀)"
CORPUS=research/data/daily_prices.sqlite
if [ -f "$CORPUS" ]; then
    local_size=$(wc -c < "$CORPUS" | tr -d ' ')
    remote_size=$(ssh "$VM" 'stat -c %s /opt/dayjaview/data/price-corpus/daily_prices.sqlite 2>/dev/null || echo 0')
    if [ "$local_size" = "$remote_size" ]; then
        echo "corpus 크기 동일($local_size bytes) — 전송 생략"
    else
        gzip -c "$CORPUS" \
            | ssh "$VM" 'sudo sh -c "gunzip -c > /opt/dayjaview/data/price-corpus/daily_prices.sqlite \
                && chown 10001:10001 /opt/dayjaview/data/price-corpus/daily_prices.sqlite" \
                && echo "corpus 반입 완료: $(stat -c %s /opt/dayjaview/data/price-corpus/daily_prices.sqlite) bytes"'
    fi
    # 파일이 실제로 놓인 뒤에만 경로를 알린다 — 경로만 있고 파일이 없으면 api가 못 뜬다.
    printf 'PRICE_CORPUS_PATH=/workspace/data/price-corpus/daily_prices.sqlite\n' \
        | ssh "$VM" 'sudo tee -a /etc/dayjaview/api.env >/dev/null && echo "PRICE_CORPUS_PATH 주입"'
else
    echo "경고: $CORPUS가 없어 건너뜀 — 사건 이후 주가 질문(gate)이 닫힌 채다" >&2
fi

say "5-3) 회사 온톨로지 입력 전송 (KRX 사명 이력 색인)"
KRX_NAMES=research/ontology/krx_name_windows.json
if [ -f "$KRX_NAMES" ]; then
    gzip -c "$KRX_NAMES" \
        | ssh "$VM" 'sudo sh -c "gunzip -c > /opt/dayjaview/data/ontology/krx_name_windows.json \
            && chown 10001:10001 /opt/dayjaview/data/ontology/krx_name_windows.json" \
            && echo "사명 색인 반입 완료"'
else
    echo "경고: $KRX_NAMES가 없어 건너뜀 — 단계 3·4 적재 시 과거 사명 해석이 빠진다" >&2
fi

say "6) 이미지 빌드 (ARM64 네이티브 — 첫 회는 수 분 걸린다)"
ssh "$VM" "cd /opt/dayjaview/repo/infra/deployment && sudo docker compose -f compose.production.yml build"

say "7) 기동 (migrate → api 순서는 compose가 보장)"
ssh "$VM" "cd /opt/dayjaview/repo/infra/deployment && sudo docker compose -f compose.production.yml up -d"

say "8) api healthy 대기 (최대 5분)"
ssh "$VM" 'for i in $(seq 1 60); do
    st=$(sudo docker inspect --format "{{.State.Health.Status}}" dayjaview-production-api-1 2>/dev/null || echo 없음)
    echo "api 상태: $st"
    [ "$st" = "healthy" ] && exit 0
    sleep 5
done
echo "5분 내 healthy가 아님 — 로그를 확인하세요:"
sudo docker compose -f /opt/dayjaview/repo/infra/deployment/compose.production.yml logs --tail 50 api
exit 1'

say "완료"
echo "다음 확인(운영자 PC에서): curl -fsS https://api.dayjaview.duckdns.org/api/health"
echo "  - 인증서 첫 발급에 1~2분 걸릴 수 있다."
echo "백업 암호문 보관(필수, 값은 직접 확인): ssh $VM 'sudo cat /etc/dayjaview/backup.passphrase'"
echo "  - 출력값을 비밀번호 관리자에 저장해 두세요. 잃으면 백업을 못 풉니다."
